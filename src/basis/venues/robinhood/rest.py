"""Thin async client for the official Robinhood Crypto Trading API (read-only in M0).

v1 routes to market makers with no explicit fee (cost is in the spread); v2 routes to partner
exchanges with maker/taker fee tiers. Quotes from both are recorded so their all-in cost can be
compared. There is no WebSocket and no sandbox: market data is polled under a shared
account-wide budget of 100 req/min.
"""

from __future__ import annotations

import time
from collections.abc import Sequence
from typing import Literal
from urllib.parse import urlencode

import httpx

from basis.core.http import RawResponse
from basis.core.ratelimit import TokenBucket
from basis.venues.robinhood.auth import RobinhoodSigner

BASE_URL = "https://trading.robinhood.com"

type ApiVersion = Literal["v1", "v2"]
type BookSide = Literal["bid", "ask", "both"]


class RobinhoodHTTPError(RuntimeError):
    def __init__(self, status: int, body: str, path: str) -> None:
        super().__init__(f"Robinhood {status} on {path}: {body[:300]}")
        self.status, self.body, self.path = status, body, path


def build_path(path: str, params: Sequence[tuple[str, str]] = ()) -> str:
    """Query string is part of the signed path, so build it ourselves (stable order)."""
    return f"{path}?{urlencode(list(params))}" if params else path


class RobinhoodRest:
    def __init__(
        self,
        signer: RobinhoodSigner,
        *,
        limiter: TokenBucket | None = None,
        timeout_s: float = 10.0,
        client: httpx.AsyncClient | None = None,
    ) -> None:
        self.signer = signer
        self.limiter = limiter
        self._client = client or httpx.AsyncClient(base_url=BASE_URL, timeout=timeout_s)

    async def aclose(self) -> None:
        await self._client.aclose()

    async def request(self, method: str, path: str, body: str = "") -> RawResponse:
        if self.limiter is not None:
            await self.limiter.acquire()
        headers = self.signer.headers(int(time.time()), path, method, body)
        if body:
            headers["Content-Type"] = "application/json; charset=utf-8"
        resp = await self._client.request(
            method, path, content=body.encode() if body else None, headers=headers
        )
        if resp.status_code >= 400:
            raise RobinhoodHTTPError(resp.status_code, resp.text, path)
        return RawResponse(resp.status_code, resp.text, path)

    # --- Market data ---------------------------------------------------------------------

    async def best_bid_ask(self, version: ApiVersion, symbols: Sequence[str]) -> RawResponse:
        path = build_path(
            f"/api/{version}/crypto/marketdata/best_bid_ask/", [("symbol", s) for s in symbols]
        )
        return await self.request("GET", path)

    async def estimated_price(
        self, version: ApiVersion, symbol: str, side: BookSide, quantities: Sequence[str]
    ) -> RawResponse:
        if not 1 <= len(quantities) <= 10:
            raise ValueError("estimated_price takes 1-10 quantities")
        base = (
            "/api/v1/crypto/marketdata/estimated_price/"
            if version == "v1"
            else "/api/v2/crypto/trading/estimated_price/"
        )
        path = build_path(
            base, [("symbol", symbol), ("side", side), ("quantity", ",".join(quantities))]
        )
        return await self.request("GET", path)

    async def trading_pairs(self, version: ApiVersion, symbols: Sequence[str]) -> RawResponse:
        path = build_path(
            f"/api/{version}/crypto/trading/trading_pairs/", [("symbol", s) for s in symbols]
        )
        return await self.request("GET", path)

    # --- Account (read-only) -------------------------------------------------------------

    async def accounts(self, version: ApiVersion = "v2") -> RawResponse:
        return await self.request("GET", f"/api/{version}/crypto/trading/accounts/")

    async def holdings(self, account_number: str) -> RawResponse:
        path = build_path("/api/v2/crypto/trading/holdings/", [("account_number", account_number)])
        return await self.request("GET", path)
