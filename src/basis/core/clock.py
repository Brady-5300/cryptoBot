"""Time sources.

Every recorded message carries two local timestamps:

* ``wall_ns``: UTC wall clock (``time.time_ns``). Comparable with exchange timestamps, but can
  jump when NTP steps the clock.
* ``mono_ns``: monotonic clock. Never jumps, so it gives true inter-arrival times within one
  process run. Not comparable across restarts.

Replay orders events by local receive time (what we knew, when we knew it), never by exchange
time, which would leak information from the future into a backtest.
"""

from __future__ import annotations

import time
from dataclasses import dataclass
from typing import Protocol


@dataclass(frozen=True, slots=True)
class Stamp:
    wall_ns: int
    mono_ns: int


class Clock(Protocol):
    def now(self) -> Stamp: ...


class WallClock:
    def now(self) -> Stamp:
        return Stamp(time.time_ns(), time.monotonic_ns())


class FixedClock:
    """Deterministic clock for tests; ``advance`` moves both components together."""

    def __init__(self, wall_ns: int = 0, mono_ns: int = 0) -> None:
        self._wall_ns = wall_ns
        self._mono_ns = mono_ns

    def now(self) -> Stamp:
        return Stamp(self._wall_ns, self._mono_ns)

    def advance(self, ns: int) -> None:
        self._wall_ns += ns
        self._mono_ns += ns
