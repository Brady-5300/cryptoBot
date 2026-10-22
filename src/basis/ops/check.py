"""Read-only environment check: config, credentials, connectivity, account status, clock.

Never places or cancels orders. Run after adding keys::

    basis-check
"""

from __future__ import annotations

import argparse
import asyncio
import json
import os
import time
from email.utils import parsedate_to_datetime
from pathlib import Path
from typing import Any

import httpx

from basis.config import load_config, load_secrets
from basis.venues.kalshi.auth import KalshiSigner
from basis.venues.kalshi.env import ENVS
from basis.venues.kalshi.rest import KalshiHTTPError, KalshiRest
from basis.venues.robinhood.auth import RobinhoodSigner
from basis.venues.robinhood.rest import RobinhoodHTTPError, RobinhoodRest


def _ok(label: str, detail: Any = "") -> None:
    print(f"  [ok]   {label} {detail}")


def _bad(label: str, detail: Any = "") -> None:
    print(f"  [FAIL] {label} {detail}")


async def check_clock() -> None:
    """Coarse check (1s resolution) against Kalshi's HTTP Date header."""
    async with httpx.AsyncClient(timeout=10) as c:
        t0 = time.time()
        r = await c.get(ENVS["prod"].rest_base + "/trade-api/v2/margin/exchange/status")
        t1 = time.time()
    server = parsedate_to_datetime(r.headers["date"]).timestamp()
    offset = (t0 + t1) / 2 - server
    label = f"local clock vs Kalshi Date header: {offset:+.2f}s (header has 1s resolution)"
    (_ok if abs(offset) < 1.5 else _bad)(label)


async def check_kalshi(cfg: dict[str, Any]) -> None:
    print("Kalshi")
    secrets = load_secrets()
    for env_name in ("prod", "demo"):
        env = ENVS[env_name]
        creds = secrets.kalshi(env_name)
        signer = KalshiSigner.from_file(creds.key_id, creds.private_key_path) if creds else None
        rest = KalshiRest(env, signer)
        try:
            markets = {m["ticker"]: m for m in (await rest.markets()).json()["markets"]}
            suffix = "" if env_name == "prod" else "1"
            for t in cfg["kalshi"]["tickers"]:
                m = markets.get(t + suffix)
                if m is None:
                    _bad(f"{env_name} {t + suffix}", "not listed")
                else:
                    _ok(
                        f"{env_name} {t + suffix}",
                        f"status={m['status']} contract_size={m['contract_size']} "
                        f"bid/ask={m['bid']}/{m['ask']} ref={m.get('reference_price', {})}",
                    )
            if signer is None:
                print(f"  [--]   {env_name}: no credentials configured (public data only)")
                continue
            for name, call in [
                ("margin enabled", rest.margin_enabled),
                ("balance", rest.balance),
                ("fee tier rates", rest.fee_tier_rates),
                ("API limits", rest.api_limits),
            ]:
                try:
                    _ok(f"{env_name} {name}:", json.dumps((await call()).json())[:300])
                except KalshiHTTPError as e:
                    _bad(f"{env_name} {name}:", f"HTTP {e.status} {e.body[:200]}")
        finally:
            await rest.aclose()


async def check_robinhood(cfg: dict[str, Any]) -> None:
    print("Robinhood")
    creds = load_secrets().robinhood
    if creds is None:
        print("  [--]   no credentials configured; Robinhood quotes will not be recorded")
        return
    rest = RobinhoodRest(RobinhoodSigner(creds.api_key, creds.private_key_b64))
    try:
        for name, call in [
            ("v1 account", lambda: rest.accounts("v1")),
            ("v2 accounts", lambda: rest.accounts("v2")),
            ("v1 best bid/ask", lambda: rest.best_bid_ask("v1", cfg["robinhood"]["symbols"])),
            ("v2 best bid/ask", lambda: rest.best_bid_ask("v2", cfg["robinhood"]["symbols"])),
            ("v2 trading pairs", lambda: rest.trading_pairs("v2", cfg["robinhood"]["symbols"])),
        ]:
            try:
                body = (await call()).json()
                if "account" in name:  # don't echo account numbers / buying power
                    body = {"keys": sorted(body if isinstance(body, dict) else {})}
                _ok(f"{name}:", json.dumps(body)[:400])
            except RobinhoodHTTPError as e:
                _bad(f"{name}:", f"HTTP {e.status} {e.body[:200]}")
    finally:
        await rest.aclose()


def check_paths(cfg: dict[str, Any]) -> None:
    data_dir = Path(cfg["paths"]["data_dir"]).resolve()
    print(f"data dir: {data_dir}")
    synced = [Path(v).resolve() for k, v in os.environ.items() if k.lower().startswith("onedrive")]
    if any(data_dir.is_relative_to(root) for root in synced):
        print("  [WARN] data dir is inside OneDrive; move it out (sync fights constant appends)")


async def run(cfg: dict[str, Any]) -> None:
    await check_clock()
    await check_kalshi(cfg)
    await check_robinhood(cfg)


def main(argv: list[str] | None = None) -> None:
    ap = argparse.ArgumentParser(description="Read-only environment check")
    ap.add_argument("--config", type=Path, action="append", default=[])
    args = ap.parse_args(argv)
    cfg = load_config(*args.config)
    check_paths(cfg)
    asyncio.run(run(cfg))


if __name__ == "__main__":
    main()
