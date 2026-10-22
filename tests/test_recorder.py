import asyncio
from pathlib import Path
from typing import Any

import pytest

from basis.config import Secrets, load_config
from basis.core.http import RawResponse
from basis.data.backfill import flatten_candle
from basis.data.rawlog import iter_raw
from basis.data.recorder import Recorder
from basis.venues.kalshi.rest import KalshiHTTPError


def _recorder(tmp_path: Path) -> Recorder:
    cfg = load_config()
    cfg["paths"]["data_dir"] = str(tmp_path)
    return Recorder(cfg, Secrets(None, None, None))


def _records(root: Path, pattern: str) -> list[dict[str, Any]]:
    return [r for p in sorted(root.glob(pattern)) for r in iter_raw(p)]


async def test_poll_records_responses_with_latency_envelope(tmp_path: Path) -> None:
    rec = _recorder(tmp_path)
    calls = 0

    async def fetch() -> RawResponse:
        nonlocal calls
        calls += 1
        if calls == 3:
            rec.stop.set()
        return RawResponse(200, f'{{"n":{calls}}}', "/margin/markets")

    await asyncio.wait_for(rec.poll("kalshi", "rest_markets", 0.01, fetch), timeout=5)
    for w in rec.writers.values():
        w.close()
    rows = _records(tmp_path, "raw/kalshi/rest_markets/*/*.jsonl")
    assert [r["d"]["body"]["n"] for r in rows] == [1, 2, 3]
    assert all(r["t"] >= r["d"]["sent_ns"] for r in rows)
    assert rows[0]["d"]["path"] == "/margin/markets"


async def test_poll_records_errors_to_meta_and_keeps_going(tmp_path: Path) -> None:
    rec = _recorder(tmp_path)
    calls = 0

    async def fetch() -> RawResponse:
        nonlocal calls
        calls += 1
        if calls == 1:
            raise KalshiHTTPError(429, "slow down", "/margin/markets")
        rec.stop.set()
        return RawResponse(200, "{}", "/margin/markets")

    await asyncio.wait_for(rec.poll("kalshi", "rest_markets", 0.01, fetch), timeout=5)
    for w in rec.writers.values():
        w.close()
    meta = _records(tmp_path, "raw/kalshi/meta/*/*.jsonl")
    assert meta[0]["d"]["event"] == "http_error" and meta[0]["d"]["status"] == 429
    assert len(_records(tmp_path, "raw/kalshi/rest_markets/*/*.jsonl")) == 1


def test_no_credentials_means_public_rest_only(tmp_path: Path) -> None:
    rec = _recorder(tmp_path)
    kalshi = rec.kalshi_tasks()
    robinhood = rec.robinhood_tasks()
    # markets + one funding-estimate poller per ticker; no WS, no Robinhood
    assert len(kalshi) == 1 + len(rec.cfg["kalshi"]["tickers"])
    assert robinhood == []
    for c in kalshi:
        c.close()  # type: ignore[attr-defined]


def test_flatten_candle_handles_null_trade_prices() -> None:
    c = {
        "end_period_ts": 1,
        "bid": {"open": "8.1708", "high": "8.1790", "low": "8.1703", "close": "8.1786"},
        "ask": {"open": "8.1711", "high": "8.1806", "low": "8.1711", "close": "8.1792"},
        "price": {
            "open": None,
            "high": None,
            "low": None,
            "close": None,
            "mean": None,
            "previous": "8.1712",
        },
        "volume": "0.00",
        "open_interest": "2257873.00",
    }
    row = flatten_candle("KXBTCPERP", c)
    assert row["bid_close_u"] == 81_786 and row["ask_high_u"] == 81_806
    assert row["price_close_u"] is None and row["price_previous_u"] == 81_712
    assert row["open_interest_q"] == 2_257_873 * 10**8


@pytest.mark.network
async def test_live_public_markets_endpoint() -> None:
    from basis.venues.kalshi.env import PROD
    from basis.venues.kalshi.rest import KalshiRest

    rest = KalshiRest(PROD)
    try:
        markets = {m["ticker"]: m for m in (await rest.markets()).json()["markets"]}
    finally:
        await rest.aclose()
    btc = markets["KXBTCPERP"]
    assert btc["contract_size"] == "0.000100"
    assert "reference_price" in btc and "settlement_mark_price" in btc
