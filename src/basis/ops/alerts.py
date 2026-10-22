"""Phone alerts via ntfy.sh. Disabled unless ``NTFY_TOPIC`` is configured. Alerts never raise:
a failing alert channel must not take down the process it is reporting on."""

from __future__ import annotations

import logging
import time

import httpx

log = logging.getLogger(__name__)


class Alerter:
    def __init__(self, topic: str | None, *, min_interval_s: float = 300.0) -> None:
        self.topic = topic
        self.min_interval_s = min_interval_s
        self._last_sent: dict[str, float] = {}

    async def send(self, key: str, title: str, message: str, *, priority: int = 4) -> None:
        """``key`` dedupes: the same key alerts at most once per ``min_interval_s``."""
        log.warning("ALERT %s: %s", title, message)
        if not self.topic:
            return
        now = time.monotonic()
        if now - self._last_sent.get(key, -1e18) < self.min_interval_s:
            return
        self._last_sent[key] = now
        try:
            async with httpx.AsyncClient(timeout=10) as c:
                await c.post(
                    f"https://ntfy.sh/{self.topic}",
                    content=message.encode(),
                    headers={"Title": title, "Priority": str(priority)},
                )
        except httpx.HTTPError as e:
            log.error("alert delivery failed: %r", e)
