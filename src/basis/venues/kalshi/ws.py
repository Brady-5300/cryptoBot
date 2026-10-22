"""Kalshi perps WebSocket client with reconnect and sequence-gap detection.

The client hands every raw text frame to ``on_message`` before doing anything else with it, so
recording never depends on parsing. Connection lifecycle and gaps are reported to ``on_meta``
as small dicts; the recorder stores them in a separate stream so replay knows where book state
is invalid (between a disconnect/gap and the next snapshot).

Auth: the handshake carries the same three KALSHI-ACCESS-* headers as REST, signed over
``GET`` + the WS path (``/trade-api/ws/v2/margin``). Kalshi pings every 10s; the websockets
library answers pongs automatically, and our own pings detect a dead socket.
"""

from __future__ import annotations

import asyncio
import contextlib
import json
import logging
import random
from collections.abc import Callable, Sequence
from dataclasses import dataclass, field
from typing import Any

from websockets.asyncio.client import ClientConnection, connect
from websockets.exceptions import ConnectionClosed, InvalidStatus

from basis.core.clock import Clock, Stamp, WallClock
from basis.venues.kalshi.auth import KalshiSigner
from basis.venues.kalshi.env import KalshiEnv

log = logging.getLogger(__name__)

SNAPSHOT_TYPES = frozenset({"orderbook_snapshot"})


@dataclass
class SeqTracker:
    """Per-subscription sequence checking. Snapshots (re)start a sequence."""

    expected: dict[int, int] = field(default_factory=dict)

    def observe(self, sid: int, seq: int, *, is_snapshot: bool) -> tuple[int, int] | None:
        """Return ``(expected, got)`` on a gap, else ``None``."""
        if is_snapshot or sid not in self.expected:
            self.expected[sid] = seq + 1
            return None
        want = self.expected[sid]
        self.expected[sid] = seq + 1
        if seq != want:
            return (want, seq)
        return None

    def reset(self) -> None:
        self.expected.clear()


def backoff_delay(attempt: int, base_s: float = 1.0, cap_s: float = 60.0) -> float:
    """Exponential backoff with full jitter."""
    return float(random.uniform(0, min(cap_s, base_s * 2**attempt)))


class KalshiWs:
    def __init__(
        self,
        env: KalshiEnv,
        signer: KalshiSigner,
        tickers: Sequence[str],
        channels: Sequence[str],
        on_message: Callable[[str, Stamp], None],
        on_meta: Callable[[dict[str, Any]], None],
        *,
        clock: Clock | None = None,
    ) -> None:
        self.env, self.signer = env, signer
        self.tickers, self.channels = list(tickers), list(channels)
        self.on_message, self.on_meta = on_message, on_meta
        self.clock = clock or WallClock()
        self.seq = SeqTracker()
        self._cmd_id = 0
        self.connected = False

    def _next_id(self) -> int:
        self._cmd_id += 1
        return self._cmd_id

    def subscribe_commands(self) -> list[dict[str, Any]]:
        cmds = []
        for ch in self.channels:
            params: dict[str, Any] = {"channels": [ch], "market_tickers": self.tickers}
            if ch == "ticker":
                params["send_initial_snapshot"] = True
            cmds.append({"id": self._next_id(), "cmd": "subscribe", "params": params})
        return cmds

    async def run(self, stop: asyncio.Event) -> None:
        attempt = 0
        while not stop.is_set():
            started = self.clock.now().mono_ns
            try:
                await self._session(stop)
            except InvalidStatus as e:
                status = e.response.status_code
                self._meta("handshake_rejected", status=status)
                if status in (401, 403):
                    log.error("Kalshi WS auth rejected (%s); check margin API key", status)
            except (ConnectionClosed, OSError, TimeoutError) as e:
                self._meta("disconnected", error=repr(e))
            except Exception as e:  # never let the recorder die on one bad session
                log.exception("Kalshi WS session crashed")
                self._meta("session_error", error=repr(e))
            finally:
                self.connected = False
                self.seq.reset()
            if stop.is_set():
                break
            # A session that stayed up for a while resets the backoff.
            if self.clock.now().mono_ns - started > 60e9:
                attempt = 0
            delay = backoff_delay(attempt)
            attempt += 1
            log.warning("Kalshi WS reconnecting in %.1fs", delay)
            with contextlib.suppress(TimeoutError):
                await asyncio.wait_for(stop.wait(), timeout=delay)

    async def _session(self, stop: asyncio.Event) -> None:
        stamp = self.clock.now()
        headers = self.signer.headers("GET", self.env.ws_path, stamp.wall_ns // 1_000_000)
        async with connect(
            self.env.ws_url,
            additional_headers=headers,
            ping_interval=20,
            ping_timeout=20,
            max_size=2**24,
            open_timeout=15,
        ) as ws:
            self.connected = True
            self._meta("connected")
            for cmd in self.subscribe_commands():
                await ws.send(json.dumps(cmd))
            stop_task = asyncio.create_task(stop.wait())
            try:
                while True:
                    recv_task = asyncio.create_task(ws.recv())
                    done, _ = await asyncio.wait(
                        {recv_task, stop_task}, return_when=asyncio.FIRST_COMPLETED
                    )
                    if stop_task in done:
                        recv_task.cancel()
                        self._meta("closing")
                        return
                    raw = recv_task.result()
                    text = raw if isinstance(raw, str) else raw.decode("utf-8")
                    await self._handle(ws, text, self.clock.now())
            finally:
                stop_task.cancel()

    async def _handle(self, ws: ClientConnection, text: str, stamp: Stamp) -> None:
        self.on_message(text, stamp)  # record first, interpret second
        try:
            msg = json.loads(text)
        except json.JSONDecodeError:
            log.warning("non-JSON frame from Kalshi: %.200s", text)
            return
        mtype = msg.get("type")
        if mtype == "error":
            log.error("Kalshi WS error: %s", msg.get("msg"))
            return
        sid, seq = msg.get("sid"), msg.get("seq")
        if isinstance(sid, int) and isinstance(seq, int):
            gap = self.seq.observe(sid, seq, is_snapshot=mtype in SNAPSHOT_TYPES)
            if gap is not None:
                self._meta("seq_gap", sid=sid, expected=gap[0], got=gap[1])
                log.warning("Kalshi seq gap sid=%s expected=%s got=%s; resnapshot", sid, *gap)
                await ws.send(
                    json.dumps(
                        {
                            "id": self._next_id(),
                            "cmd": "update_subscription",
                            "params": {"sids": [sid], "action": "get_snapshot"},
                        }
                    )
                )

    def _meta(self, event: str, **fields: Any) -> None:
        stamp = self.clock.now()
        self.on_meta(
            {
                "event": event,
                "venue": "kalshi",
                "env": self.env.name,
                "wall_ns": stamp.wall_ns,
                **fields,
            }
        )
