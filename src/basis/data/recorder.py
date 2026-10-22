"""Market-data recorder: the first thing that must run 24/7.

Sources (each optional, so it runs with whatever credentials exist):

* Kalshi perps WebSocket (needs margin API key): ticker, trade, orderbook_delta.
* Kalshi public REST (no key): ``/margin/markets`` every second (reference + mark prices, best
  bid/ask for all markets), funding estimates, optional REST book snapshots. Runs even when the
  WS is up, as an independent cross-check and gap filler.
* Robinhood Crypto API (needs API key): best bid/ask for v1 and v2, plus size-aware
  ``estimated_price`` ladders, under a self-imposed rate budget.

Everything goes to the raw log (see ``rawlog``). Usage::

    basis-record                     # config/base.toml
    basis-record --config config/x.toml --duration 60
"""

from __future__ import annotations

import argparse
import asyncio
import contextlib
import json
import logging
import signal
import sys
import time
from collections.abc import Awaitable, Callable
from pathlib import Path
from typing import Any

import httpx

from basis.config import Secrets, load_config, load_secrets
from basis.core.clock import Clock, Stamp, WallClock
from basis.core.http import RawResponse
from basis.core.ratelimit import TokenBucket
from basis.data.rawlog import RawWriter, compress_file, wrap_rest
from basis.ops.alerts import Alerter
from basis.venues.kalshi.auth import KalshiSigner
from basis.venues.kalshi.env import ENVS
from basis.venues.kalshi.rest import KalshiHTTPError, KalshiRest
from basis.venues.kalshi.ws import KalshiWs, backoff_delay
from basis.venues.robinhood.auth import RobinhoodSigner
from basis.venues.robinhood.rest import ApiVersion, RobinhoodHTTPError, RobinhoodRest

log = logging.getLogger("basis.recorder")

type Fetch = Callable[[], Awaitable[RawResponse]]


class Recorder:
    def __init__(self, cfg: dict[str, Any], secrets: Secrets, *, clock: Clock | None = None):
        self.cfg, self.secrets = cfg, secrets
        self.clock = clock or WallClock()
        self.root = Path(cfg["paths"]["data_dir"])
        self.writers: dict[str, RawWriter] = {}
        self.alerter = Alerter(secrets.ntfy_topic)
        self.stop = asyncio.Event()
        self._closers: list[Callable[[], Awaitable[None]]] = []
        self._stale: set[str] = set()

    # --- writers ---------------------------------------------------------------------

    def writer(self, venue: str, stream: str) -> RawWriter:
        key = f"{venue}/{stream}"
        if key not in self.writers:
            self.writers[key] = RawWriter(self.root, venue, stream)
        return self.writers[key]

    def write(self, venue: str, stream: str, raw: str, stamp: Stamp) -> None:
        self.writer(venue, stream).write(raw, stamp.wall_ns, stamp.mono_ns)

    def meta(self, venue: str, event: dict[str, Any]) -> None:
        stamp = self.clock.now()
        self.write(venue, "meta", json.dumps(event, separators=(",", ":")), stamp)

    # --- generic poller ---------------------------------------------------------------

    async def poll(self, venue: str, stream: str, interval_s: float, fetch: Fetch) -> None:
        """Call ``fetch`` on a fixed grid (no drift; missed slots are skipped, not queued)
        and record each response. Errors are recorded to ``meta`` and backed off."""
        failures = 0
        next_at = time.monotonic()
        while not self.stop.is_set():
            sent = self.clock.now()
            try:
                resp = await fetch()
                stamp = self.clock.now()
                self.write(
                    venue, stream, wrap_rest(resp.path, sent.wall_ns, resp.status, resp.text),
                    stamp,
                )
                failures = 0
            except (KalshiHTTPError, RobinhoodHTTPError) as e:
                failures += 1
                self.meta(venue, {"event": "http_error", "stream": stream,
                                  "status": e.status, "body": e.body[:500]})
                log.warning("%s/%s HTTP %s: %.200s", venue, stream, e.status, e.body)
                if e.status in (401, 403):
                    await self.alerter.send(f"{venue}-auth", f"{venue} auth failed",
                                            f"{stream}: HTTP {e.status}")
            except (httpx.HTTPError, OSError) as e:
                failures += 1
                self.meta(venue, {"event": "transport_error", "stream": stream,
                                  "error": repr(e)})
                log.warning("%s/%s transport error: %r", venue, stream, e)
            if failures:
                delay = max(interval_s, backoff_delay(failures - 1, base_s=interval_s))
                next_at = time.monotonic() + delay
            else:
                next_at += interval_s
                now = time.monotonic()
                if next_at < now:  # fell behind: skip missed slots
                    next_at = now + interval_s - ((now - next_at) % interval_s)
            with contextlib.suppress(TimeoutError):
                await asyncio.wait_for(self.stop.wait(), timeout=next_at - time.monotonic())

    # --- sources ----------------------------------------------------------------------

    def kalshi_tasks(self) -> list[Awaitable[None]]:
        kcfg = self.cfg["kalshi"]
        env = ENVS[kcfg["env"]]
        tickers: list[str] = kcfg["tickers"]
        creds = self.secrets.kalshi(env.name)
        signer = KalshiSigner.from_file(creds.key_id, creds.private_key_path) if creds else None
        rest = KalshiRest(env, signer)
        self._closers.append(rest.aclose)
        tasks: list[Awaitable[None]] = [
            self.poll("kalshi", "rest_markets", kcfg["markets_poll_s"], rest.markets),
        ]
        if (fs := kcfg.get("funding_estimate_poll_s", 0)) > 0:
            for t in tickers:
                tasks.append(self.poll("kalshi", f"rest_funding_est/{t}", fs,
                                       lambda t=t: rest.funding_estimate(t)))
        if (ob := kcfg.get("orderbook_poll_s", 0)) > 0:
            tasks.append(self.poll("kalshi", "rest_orderbooks", ob,
                                   lambda: rest.orderbooks(tickers)))
        if signer is None:
            log.warning("no Kalshi %s key: WebSocket disabled, recording public REST only",
                        env.name)
            self.meta("kalshi", {"event": "ws_disabled", "reason": "no_credentials"})
        else:
            ws = KalshiWs(
                env, signer, tickers, kcfg["ws_channels"],
                on_message=lambda raw, stamp: self.write("kalshi", "ws", raw, stamp),
                on_meta=lambda ev: self.meta("kalshi", ev),
                clock=self.clock,
            )
            tasks.append(ws.run(self.stop))
        return tasks

    def robinhood_tasks(self) -> list[Awaitable[None]]:
        rcfg = self.cfg["robinhood"]
        creds = self.secrets.robinhood
        if creds is None:
            log.warning("no Robinhood credentials: Robinhood quotes NOT recorded")
            self.meta("robinhood", {"event": "disabled", "reason": "no_credentials"})
            return []
        per_min = float(rcfg["max_requests_per_min"])
        rest = RobinhoodRest(
            RobinhoodSigner(creds.api_key, creds.private_key_b64),
            limiter=TokenBucket(per_min / 60.0, capacity=3),
        )
        self._closers.append(rest.aclose)
        symbols: list[str] = rcfg["symbols"]
        versions: list[ApiVersion] = rcfg["api_versions"]
        ladders: dict[str, list[str]] = rcfg.get("estimated_price_quantities", {})

        demand = len(versions) * 60 / rcfg["best_bid_ask_poll_s"]
        demand += len(versions) * 2 * len(ladders) * 60 / rcfg["estimated_price_poll_s"]
        if demand > per_min:
            log.warning("Robinhood polling wants %.0f req/min > budget %.0f; the limiter "
                        "will stretch intervals", demand, per_min)

        tasks: list[Awaitable[None]] = []
        for v in versions:
            tasks.append(self.poll("robinhood", f"bba_{v}", rcfg["best_bid_ask_poll_s"],
                                   lambda v=v: rest.best_bid_ask(v, symbols)))
            for sym, qtys in ladders.items():
                for side in ("bid", "ask"):
                    tasks.append(self.poll(
                        "robinhood", f"est_{v}/{sym}", rcfg["estimated_price_poll_s"],
                        lambda v=v, sym=sym, side=side, q=qtys: rest.estimated_price(
                            v, sym, side, q),
                    ))
        return tasks

    # --- housekeeping -------------------------------------------------------------------

    async def housekeeping(self) -> None:
        rec = self.cfg["recorder"]
        stats_every, stale_after = rec["stats_log_s"], rec["stale_after_s"]
        heartbeat = self.root / rec["heartbeat_file"]
        last_stats, last_counts = time.monotonic(), {k: 0 for k in self.writers}
        while not self.stop.is_set():
            with contextlib.suppress(TimeoutError):
                await asyncio.wait_for(self.stop.wait(), timeout=1.0)
            for w in self.writers.values():
                w.flush()
            for w in list(self.writers.values()):
                for path in w.take_pending():
                    await asyncio.to_thread(self._compress, path)
            now_stamp = self.clock.now()
            heartbeat.write_text(json.dumps({"wall_ns": now_stamp.wall_ns,
                                             "streams": len(self.writers)}))
            await self._check_stale(now_stamp, stale_after)
            if time.monotonic() - last_stats >= stats_every:
                rates = {k: w.lines_written - last_counts.get(k, 0)
                         for k, w in sorted(self.writers.items())}
                log.info("lines in last %.0fs: %s", stats_every, rates)
                last_counts = {k: w.lines_written for k, w in self.writers.items()}
                last_stats = time.monotonic()

    async def _check_stale(self, now: Stamp, stale_after_s: float) -> None:
        for key, w in self.writers.items():
            if key.endswith("/meta") or w.last_write_mono_ns is None:
                continue
            silent_s = (now.mono_ns - w.last_write_mono_ns) / 1e9
            if silent_s > stale_after_s and key not in self._stale:
                self._stale.add(key)
                await self.alerter.send(f"stale-{key}", "Recorder stream stale",
                                        f"{key} silent for {silent_s:.0f}s")
            elif silent_s <= stale_after_s and key in self._stale:
                self._stale.discard(key)
                log.info("stream %s recovered", key)

    @staticmethod
    def _compress(path: Path) -> None:
        try:
            dst = compress_file(path)
            log.info("compressed %s -> %s", path.name, dst.name)
        except OSError:
            log.exception("compression failed for %s (left uncompressed)", path)

    async def sweep_leftovers(self) -> None:
        """Compress plain files from earlier hours left behind by a crash."""
        now = self.clock.now().wall_ns
        raw_root = self.root / "raw"
        if not raw_root.exists():
            return
        for stream_dir in {p.parent.parent for p in raw_root.glob("*/**/*.jsonl")}:
            rel = stream_dir.relative_to(raw_root).parts
            w = RawWriter(self.root, rel[0], "/".join(rel[1:]))
            for path in w.stale_uncompressed(now):
                await asyncio.to_thread(self._compress, path)

    async def run(self, duration_s: float | None = None) -> None:
        self.root.mkdir(parents=True, exist_ok=True)
        log.info("recording to %s", self.root)
        await self.sweep_leftovers()
        self.meta("recorder", {"event": "start", "config": self.cfg})
        sources = [*self.kalshi_tasks(), *self.robinhood_tasks()]
        tasks = [asyncio.create_task(c) for c in [*sources, self.housekeeping()]]
        loop = asyncio.get_running_loop()
        if duration_s is not None:
            loop.call_later(duration_s, self.stop.set)
        # launchd/systemd stop a job with SIGTERM: shut down cleanly (flush, close files).
        # Windows has no add_signal_handler; Ctrl+C there arrives as KeyboardInterrupt.
        if sys.platform != "win32":
            for sig in (signal.SIGTERM, signal.SIGINT):
                loop.add_signal_handler(sig, self.stop.set)
        try:
            await self.stop.wait()
        finally:
            self.stop.set()
            await asyncio.gather(*tasks, return_exceptions=True)
            self.meta("recorder", {"event": "stop"})
            for close in self._closers:
                await close()
            for w in self.writers.values():
                w.shutdown()
            for w in self.writers.values():
                for path in w.take_pending():
                    self._compress(path)
            log.info("recorder stopped")


def setup_logging(log_dir: Path | None) -> None:
    handlers: list[logging.Handler] = [logging.StreamHandler()]
    if log_dir is not None:
        from logging.handlers import TimedRotatingFileHandler

        log_dir.mkdir(parents=True, exist_ok=True)
        handlers.append(TimedRotatingFileHandler(log_dir / "recorder.log", when="midnight",
                                                 backupCount=30, utc=True, encoding="utf-8"))
    logging.basicConfig(level=logging.INFO, handlers=handlers,
                        format="%(asctime)s %(levelname)s %(name)s: %(message)s")
    logging.getLogger("httpx").setLevel(logging.WARNING)


def main(argv: list[str] | None = None) -> None:
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--config", type=Path, action="append", default=[],
                    help="extra TOML merged over config/base.toml (repeatable)")
    ap.add_argument("--duration", type=float, default=None, help="stop after N seconds")
    args = ap.parse_args(argv)
    cfg = load_config(*args.config)
    setup_logging(Path(cfg["paths"]["data_dir"]) / "logs")
    recorder = Recorder(cfg, load_secrets())
    with contextlib.suppress(KeyboardInterrupt):
        asyncio.run(recorder.run(args.duration))


if __name__ == "__main__":
    main()
