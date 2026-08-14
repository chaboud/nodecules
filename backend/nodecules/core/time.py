"""Temporal primitives for nodecules.

All times are integer milliseconds, meeting-relative unless explicitly stated
otherwise. `TimeRange` is closed-open: `[start_ms, end_ms)`.

These primitives are additive to the core engine. Static (non-temporal) nodes
never touch them. Temporal nodes on the `feat/temporality` branch carry
`TimeRange` values in their outputs and rely on a `TimeSource` for execution
cadence.
"""

from __future__ import annotations

import asyncio
import time as _time
from typing import Optional, Protocol, runtime_checkable

from pydantic import BaseModel, Field, model_validator


class TimeRange(BaseModel):
    """A closed-open time interval in meeting-relative milliseconds.

    Semantics are `[start_ms, end_ms)`. `start_ms` is inclusive; `end_ms` is
    exclusive. Zero-duration ranges are rejected so `intersects` and
    `contains` have unambiguous semantics.
    """

    start_ms: int = Field(ge=0)
    end_ms: int = Field(ge=0)

    model_config = {"frozen": True}

    @model_validator(mode="after")
    def _validate_bounds(self) -> "TimeRange":
        if self.end_ms <= self.start_ms:
            raise ValueError(
                f"TimeRange end_ms ({self.end_ms}) must be > start_ms ({self.start_ms})"
            )
        return self

    def duration_ms(self) -> int:
        return self.end_ms - self.start_ms

    def contains(self, ms: int) -> bool:
        return self.start_ms <= ms < self.end_ms

    def intersects(self, other: "TimeRange") -> bool:
        return self.start_ms < other.end_ms and other.start_ms < self.end_ms

    def intersection(self, other: "TimeRange") -> Optional["TimeRange"]:
        lo = max(self.start_ms, other.start_ms)
        hi = min(self.end_ms, other.end_ms)
        if lo >= hi:
            return None
        return TimeRange(start_ms=lo, end_ms=hi)

    def union(self, other: "TimeRange") -> "TimeRange":
        """Closed-open union. Returns the smallest TimeRange covering both.

        Does NOT require overlap; gaps are bridged. Callers that need to
        reject disjoint unions should check `intersects` first.
        """
        return TimeRange(
            start_ms=min(self.start_ms, other.start_ms),
            end_ms=max(self.end_ms, other.end_ms),
        )

    def shift(self, delta_ms: int) -> "TimeRange":
        if self.start_ms + delta_ms < 0:
            raise ValueError(
                f"Cannot shift TimeRange {self} by {delta_ms}; result would be negative"
            )
        return TimeRange(
            start_ms=self.start_ms + delta_ms,
            end_ms=self.end_ms + delta_ms,
        )


@runtime_checkable
class TimeSource(Protocol):
    """Abstraction over 'what time is it now?' for the scheduler.

    Three kinds ship in the core library: `WallClock` (live), `FileClock`
    (batch), `ManualClock` (interactive review). Schedulers and temporal
    nodes go through this interface instead of calling `time.time()` or
    `datetime.now()` directly.
    """

    async def now_ms(self) -> int: ...

    async def wait_until(self, ms: int) -> None: ...

    def is_closed(self) -> bool: ...


class WallClock:
    """Monotonic wall-clock TimeSource for live mode.

    `now_ms()` returns milliseconds elapsed since `__init__` (or since the
    most recent `reset()` call). `wait_until` sleeps. Calling `close()`
    causes `is_closed()` to return `True`; future `wait_until` calls return
    immediately.
    """

    def __init__(self) -> None:
        self._origin = _time.monotonic()
        self._closed = False

    def reset(self) -> None:
        self._origin = _time.monotonic()

    async def now_ms(self) -> int:
        return int((_time.monotonic() - self._origin) * 1000)

    async def wait_until(self, ms: int) -> None:
        if self._closed:
            return
        target = self._origin + (ms / 1000.0)
        delay = target - _time.monotonic()
        if delay > 0:
            await asyncio.sleep(delay)

    def is_closed(self) -> bool:
        return self._closed

    def close(self) -> None:
        self._closed = True


class FileClock:
    """Caller-driven TimeSource for batch mode.

    Suitable for deterministic offline processing: the demuxer or the
    scheduler advances the clock explicitly as upstream windows close.
    `wait_until` is a no-op — the scheduler just re-queries `now_ms()` on
    the next tick.
    """

    def __init__(self, initial_ms: int = 0) -> None:
        if initial_ms < 0:
            raise ValueError(f"FileClock initial_ms must be >= 0, got {initial_ms}")
        self._now_ms = initial_ms
        self._closed = False

    async def now_ms(self) -> int:
        return self._now_ms

    async def wait_until(self, ms: int) -> None:
        # Batch mode: never sleep. The caller drives time forward.
        return

    def is_closed(self) -> bool:
        return self._closed

    def advance_to(self, ms: int) -> None:
        if ms < self._now_ms:
            raise ValueError(
                f"FileClock cannot rewind: now={self._now_ms} requested={ms}"
            )
        self._now_ms = ms

    def close(self) -> None:
        self._closed = True


class ManualClock:
    """UI-driven TimeSource for interactive review mode.

    `wait_until` blocks on an internal event; `advance_to` wakes any
    pending waiters whose target time has been reached. Used by a Review
    UI that wants to step through a meeting under user control.
    """

    def __init__(self, initial_ms: int = 0) -> None:
        if initial_ms < 0:
            raise ValueError(f"ManualClock initial_ms must be >= 0, got {initial_ms}")
        self._now_ms = initial_ms
        self._closed = False
        self._advance_event = asyncio.Event()

    async def now_ms(self) -> int:
        return self._now_ms

    async def wait_until(self, ms: int) -> None:
        while not self._closed and self._now_ms < ms:
            self._advance_event.clear()
            await self._advance_event.wait()

    def is_closed(self) -> bool:
        return self._closed

    def advance_to(self, ms: int) -> None:
        if ms < self._now_ms:
            raise ValueError(
                f"ManualClock cannot rewind: now={self._now_ms} requested={ms}"
            )
        self._now_ms = ms
        self._advance_event.set()

    def close(self) -> None:
        self._closed = True
        self._advance_event.set()


__all__ = [
    "TimeRange",
    "TimeSource",
    "WallClock",
    "FileClock",
    "ManualClock",
]
