"""Thin async REST client for Kalshi's /margin API.

Deliberately small: it signs, sends, and returns the raw body (so the recorder can store it
byte-exact) plus parsed JSON. Market-data endpoints under /margin are public; pass
``signer=None`` to call them without credentials.
"""

from __future__ import annotations

import json
import time
from typing import Any

import httpx

from basis.core.http import RawResponse
from basis.venues.kalshi.auth import KalshiSigner
from basis.venues.kalshi.env import KalshiEnv


class KalshiHTTPError(RuntimeError):
    def __init__(self, status: int, body: str, path: str) -> None:
        super().__init__(f"Kalshi {status} on {path}: {body[:300]}")
        self.status, self.body, self.path = status, body, path


class KalshiRest:
    def __init__(
        self,
        env: KalshiEnv,
        signer: KalshiSigner | None = None,
        *,
        timeout_s: float = 10.0,
        client: httpx.AsyncClient | None = None,
    ) -> None:
        self.env = env
        self.signer = signer
        self._client = client or httpx.AsyncClient(
            base_url=env.rest_base, timeout=timeout_s, headers={"Accept": "application/json"}
        )

    async def aclose(self) -> None:
        await self._client.aclose()

    async def request(
        self,
        method: str,
        endpoint: str,
        *,
        params: dict[str, Any] | None = None,
        body: Any = None,
        auth: bool = False,
    ) -> RawResponse:
        """``endpoint`` is relative to the API prefix, e.g. ``/margin/markets``."""
        path = self.env.api_prefix + endpoint
        headers: dict[str, str] = {}
        if auth:
            if self.signer is None:
                raise RuntimeError(f"{endpoint} requires Kalshi credentials")
            headers |= self.signer.headers(method, path, int(time.time() * 1000))
        content = None
        if body is not None:
            content = json.dumps(body, separators=(",", ":"))
            headers["Content-Type"] = "application/json"
        resp = await self._client.request(
            method, path, params=params, content=content, headers=headers
        )
        if resp.status_code >= 400:
            raise KalshiHTTPError(resp.status_code, resp.text, path)
        return RawResponse(resp.status_code, resp.text, path)

    # --- Public market data -------------------------------------------------------------

    async def markets(self) -> RawResponse:
        return await self.request("GET", "/margin/markets")

    async def market(self, ticker: str) -> RawResponse:
        return await self.request("GET", f"/margin/markets/{ticker}")

    async def orderbook(self, ticker: str) -> RawResponse:
        return await self.request("GET", f"/margin/markets/{ticker}/orderbook")

    async def orderbooks(self, tickers: list[str]) -> RawResponse:
        """Several books in one request (max 100 tickers)."""
        return await self.request(
            "GET", "/margin/markets/orderbooks", params={"tickers": ",".join(tickers)}
        )

    async def candlesticks(
        self, ticker: str, start_ts: int, end_ts: int, period_interval: int
    ) -> RawResponse:
        """``period_interval`` in minutes: 1, 60 or 1440. Timestamps in Unix seconds."""
        return await self.request(
            "GET",
            f"/margin/markets/{ticker}/candlesticks",
            params={"start_ts": start_ts, "end_ts": end_ts, "period_interval": period_interval},
        )

    async def trades(
        self,
        ticker: str,
        *,
        cursor: str | None = None,
        limit: int = 1000,
        min_ts: int | None = None,
        max_ts: int | None = None,
    ) -> RawResponse:
        """Newest first. ``limit`` max 1000; ``min_ts``/``max_ts`` in Unix seconds."""
        params: dict[str, Any] = {"ticker": ticker, "limit": limit}
        for k, v in (("cursor", cursor), ("min_ts", min_ts), ("max_ts", max_ts)):
            if v:
                params[k] = v
        return await self.request("GET", "/margin/trades", params=params)

    async def funding_estimate(self, ticker: str) -> RawResponse:
        return await self.request(
            "GET", "/margin/funding_rates/estimate", params={"ticker": ticker}
        )

    async def funding_history(
        self, ticker: str | None = None, start_ts: int | None = None, end_ts: int | None = None
    ) -> RawResponse:
        params = {
            k: v
            for k, v in {"ticker": ticker, "start_ts": start_ts, "end_ts": end_ts}.items()
            if v is not None
        }
        return await self.request("GET", "/margin/funding_rates/historical", params=params)

    # --- Authenticated account endpoints (read-only in M0) -------------------------------

    async def margin_enabled(self) -> RawResponse:
        return await self.request("GET", "/margin/enabled", auth=True)

    async def balance(self) -> RawResponse:
        return await self.request("GET", "/margin/balance", auth=True)

    async def positions(self) -> RawResponse:
        return await self.request("GET", "/margin/positions", auth=True)

    async def risk(self) -> RawResponse:
        return await self.request("GET", "/margin/risk", auth=True)

    async def fee_tier_rates(self) -> RawResponse:
        return await self.request("GET", "/margin/fee_tier_rates", auth=True)

    async def api_limits(self) -> RawResponse:
        return await self.request("GET", "/account/limits/perps", auth=True)
