"""Kalshi perps (margin) environments. Verified against docs.kalshi.com/margin.md, 2026-10-22."""

from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True)
class KalshiEnv:
    name: str
    rest_base: str  # scheme + host
    ws_url: str

    api_prefix: str = "/trade-api/v2"

    @property
    def ws_path(self) -> str:
        return "/" + self.ws_url.split("/", 3)[3]


PROD = KalshiEnv(
    name="prod",
    rest_base="https://external-api.kalshi.com",
    ws_url="wss://external-api-margin-ws.kalshi.com/trade-api/ws/v2/margin",
)
DEMO = KalshiEnv(
    name="demo",
    rest_base="https://external-api.demo.kalshi.co",
    ws_url="wss://external-api-margin-ws.demo.kalshi.co/trade-api/ws/v2/margin",
)
ENVS = {"prod": PROD, "demo": DEMO}
