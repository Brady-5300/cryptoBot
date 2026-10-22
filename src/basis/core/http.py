"""Venue-neutral HTTP response wrapper. The body is kept as raw text so it can be recorded
byte-exact; parse on demand."""

from __future__ import annotations

import json
from dataclasses import dataclass
from typing import Any


@dataclass(frozen=True, slots=True)
class RawResponse:
    status: int
    text: str
    path: str

    def json(self) -> Any:
        return json.loads(self.text)
