"""Backfill the history Kalshi does publish (all public, no keys):

* 1-minute candles with bid/ask/trade OHLC  -> ``backfill/kalshi/candles_1m/<ticker>.parquet``
* funding rate history (8h)                 -> ``backfill/kalshi/funding/<ticker>.parquet``
* public trades (optional, recent N days)   -> ``backfill/kalshi/trades/<ticker>.parquet``

What does NOT exist historically: reference price, mark prices, order book, Robinhood quotes.
Those only come from the live recorder.

Prices are stored as integer 1e-4 USD units (``*_u`` columns), counts as 1e-8 units.
Re-running overwrites the files: Kalshi's history is the source of truth here.
"""

from __future__ import annotations

import argparse
import asyncio
import logging
import time
from datetime import datetime
from pathlib import Path
from typing import Any

import polars as pl

from basis.config import load_config
from basis.core.fixed import parse_price, parse_qty
from basis.venues.kalshi.env import ENVS
from basis.venues.kalshi.rest import KalshiRest

log = logging.getLogger("basis.backfill")

CHUNK_S = 3 * 86_400  # ~4300 one-minute candles per request
PAUSE_S = 0.25  # be polite to a public endpoint


def _p(v: str | None) -> int | None:
    return None if v is None else parse_price(v)


def _q(v: str | None) -> int | None:
    return None if v is None else parse_qty(v)


def flatten_candle(ticker: str, c: dict[str, Any]) -> dict[str, Any]:
    row: dict[str, Any] = {"ticker": ticker, "end_period_ts": c["end_period_ts"]}
    for side in ("bid", "ask"):
        for f in ("open", "high", "low", "close"):
            row[f"{side}_{f}_u"] = _p((c.get(side) or {}).get(f))
    for f in ("open", "high", "low", "close", "mean", "previous"):
        row[f"price_{f}_u"] = _p((c.get("price") or {}).get(f))
    row["volume_q"] = _q(c.get("volume"))
    row["open_interest_q"] = _q(c.get("open_interest"))
    return row


async def first_candle_ts(rest: KalshiRest, ticker: str) -> int | None:
    now = int(time.time())
    resp = await rest.candlesticks(ticker, now - 3 * 365 * 86_400, now, 1440)
    candles = resp.json()["candlesticks"]
    return int(candles[0]["end_period_ts"]) - 86_400 if candles else None


async def backfill_candles(rest: KalshiRest, ticker: str, out: Path) -> int:
    start = await first_candle_ts(rest, ticker)
    if start is None:
        log.warning("%s: no candles", ticker)
        return 0
    end = int(time.time())
    rows: dict[int, dict[str, Any]] = {}
    t = start
    while t < end:
        resp = await rest.candlesticks(ticker, t, min(t + CHUNK_S, end), 1)
        for c in resp.json()["candlesticks"]:
            rows[int(c["end_period_ts"])] = flatten_candle(ticker, c)  # dedupe chunk edges
        t += CHUNK_S
        await asyncio.sleep(PAUSE_S)
    df = pl.DataFrame([rows[k] for k in sorted(rows)]).with_columns(
        pl.from_epoch("end_period_ts", time_unit="s").alias("end_period_utc")
    )
    out.parent.mkdir(parents=True, exist_ok=True)
    df.write_parquet(out)
    log.info("%s: %d one-minute candles -> %s", ticker, df.height, out)
    return df.height


async def backfill_funding(rest: KalshiRest, ticker: str, out: Path) -> int:
    rates = (await rest.funding_history(ticker)).json()["funding_rates"]
    df = pl.DataFrame(
        [
            {
                "ticker": r["market_ticker"],
                "funding_time_utc": datetime.fromisoformat(r["funding_time"]),
                "funding_rate": float(r["funding_rate"]),
                "mark_price_u": parse_price(r["mark_price"]),
            }
            for r in rates
        ]
    ).sort("funding_time_utc")
    out.parent.mkdir(parents=True, exist_ok=True)
    df.write_parquet(out)
    log.info("%s: %d funding prints -> %s", ticker, df.height, out)
    return df.height


async def backfill_trades(rest: KalshiRest, ticker: str, days: float, out: Path) -> int:
    min_ts = int(time.time() - days * 86_400)
    rows: list[dict[str, Any]] = []
    cursor: str | None = None
    while True:
        page = (await rest.trades(ticker, cursor=cursor, min_ts=min_ts)).json()
        for tr in page.get("trades", []):
            rows.append({
                "ticker": tr["ticker"],
                "trade_id": tr["trade_id"],
                "created_time_utc": datetime.fromisoformat(tr["created_time"]),
                "price_u": parse_price(tr["price"]),
                "count_q": parse_qty(tr["count"]),
                "taker_side": tr["taker_side"],
            })
        cursor = page.get("cursor")
        if not cursor or not page.get("trades"):
            break
        await asyncio.sleep(PAUSE_S)
    df = pl.DataFrame(rows).unique("trade_id").sort("created_time_utc") if rows else None
    if df is None:
        return 0
    out.parent.mkdir(parents=True, exist_ok=True)
    df.write_parquet(out)
    log.info("%s: %d trades (%.2f days) -> %s", ticker, df.height, days, out)
    return df.height


async def run(cfg: dict[str, Any], trades_days: float, tickers: list[str] | None) -> None:
    root = Path(cfg["paths"]["data_dir"]) / "backfill" / "kalshi"
    rest = KalshiRest(ENVS["prod"])  # history is recorded from prod regardless of trading env
    try:
        for ticker in tickers or cfg["kalshi"]["tickers"]:
            await backfill_funding(rest, ticker, root / "funding" / f"{ticker}.parquet")
            await backfill_candles(rest, ticker, root / "candles_1m" / f"{ticker}.parquet")
            if trades_days > 0:
                await backfill_trades(rest, ticker, trades_days,
                                      root / "trades" / f"{ticker}.parquet")
    finally:
        await rest.aclose()


def main(argv: list[str] | None = None) -> None:
    ap = argparse.ArgumentParser(description="Backfill public Kalshi perp history")
    ap.add_argument("--config", type=Path, action="append", default=[])
    ap.add_argument("--trades-days", type=float, default=0.0,
                    help="also fetch public trades for the last N days (can be large)")
    ap.add_argument("--ticker", action="append", default=None, help="limit to ticker(s)")
    args = ap.parse_args(argv)
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
    logging.getLogger("httpx").setLevel(logging.WARNING)
    asyncio.run(run(load_config(*args.config), args.trades_days, args.ticker))


if __name__ == "__main__":
    main()
