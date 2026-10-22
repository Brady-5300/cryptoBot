import json
from typing import Any

from cryptography.hazmat.primitives.asymmetric import ed25519

from basis.core.clock import FixedClock
from basis.venues.kalshi.auth import KalshiSigner
from basis.venues.kalshi.env import DEMO, PROD
from basis.venues.kalshi.ws import KalshiWs, SeqTracker


def test_ws_path_derived_from_url() -> None:
    assert PROD.ws_path == "/trade-api/ws/v2/margin"
    assert DEMO.ws_path == "/trade-api/ws/v2/margin"


def test_seq_tracker_detects_gaps_and_snapshots_reset() -> None:
    s = SeqTracker()
    assert s.observe(1, 5, is_snapshot=True) is None
    assert s.observe(1, 6, is_snapshot=False) is None
    assert s.observe(1, 8, is_snapshot=False) == (7, 8)
    assert s.observe(1, 9, is_snapshot=False) is None  # continues from the new position
    assert s.observe(1, 100, is_snapshot=True) is None
    assert s.observe(2, 1, is_snapshot=False) is None  # first sight of a sid is not a gap


class FakeWs:
    def __init__(self) -> None:
        self.sent: list[dict[str, Any]] = []

    async def send(self, text: str) -> None:
        self.sent.append(json.loads(text))


def _client() -> tuple[KalshiWs, list[str], list[dict[str, Any]]]:
    raws: list[str] = []
    metas: list[dict[str, Any]] = []
    ws = KalshiWs(
        PROD,
        KalshiSigner("k", ed25519.Ed25519PrivateKey.generate()),
        ["KXBTCPERP", "KXETHPERP"],
        ["ticker", "orderbook_delta"],
        on_message=lambda raw, _stamp: raws.append(raw),
        on_meta=metas.append,
        clock=FixedClock(1, 1),
    )
    return ws, raws, metas


def test_subscribe_commands_unique_ids_and_ticker_snapshot() -> None:
    ws, _, _ = _client()
    cmds = ws.subscribe_commands()
    assert [c["id"] for c in cmds] == [1, 2]
    assert cmds[0]["params"] == {
        "channels": ["ticker"],
        "market_tickers": ["KXBTCPERP", "KXETHPERP"],
        "send_initial_snapshot": True,
    }
    assert "send_initial_snapshot" not in cmds[1]["params"]


async def test_gap_triggers_resnapshot_and_everything_is_recorded() -> None:
    ws, raws, metas = _client()
    fake = FakeWs()
    frames = [
        '{"type":"orderbook_snapshot","sid":2,"seq":1,"msg":{}}',
        '{"type":"orderbook_delta","sid":2,"seq":2,"msg":{}}',
        '{"type":"orderbook_delta","sid":2,"seq":4,"msg":{}}',
        "not json",
    ]
    for f in frames:
        await ws._handle(fake, f, FixedClock().now())  # type: ignore[arg-type]
    assert raws == frames  # recorded verbatim, including the garbage frame
    assert [m["event"] for m in metas] == ["seq_gap"]
    assert metas[0] | {"wall_ns": 0} == {
        "event": "seq_gap",
        "venue": "kalshi",
        "env": "prod",
        "wall_ns": 0,
        "sid": 2,
        "expected": 3,
        "got": 4,
    }
    assert fake.sent == [
        {"id": 1, "cmd": "update_subscription", "params": {"sids": [2], "action": "get_snapshot"}}
    ]
