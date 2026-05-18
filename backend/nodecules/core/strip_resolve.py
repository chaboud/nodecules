"""Resolving declared strip access against a concrete window (PR-r2).

`strip_access.py` is the pure-data ADT — it says *how* a node reads a
strip, symbolically. This module turns a declaration plus the reading
node's concrete active window into something runnable.

Kept separate from the ADT so `strip_access.py` stays dependency-light
(Pydantic only); resolution depends on `core.time.TimeRange`.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any

from .strip_access import (
    AbsoluteMs,
    RangePattern,
    SelfWindowEnd,
    SelfWindowStart,
    TimeExpr,
)
from .time import TimeRange


def resolve_time_expr(expr: TimeExpr, window: TimeRange) -> int:
    """Resolve a symbolic `TimeExpr` to an integer ms against `window`.

    `SelfWindowStart` / `SelfWindowEnd` resolve to the reading node's
    active window bounds; `AbsoluteMs` resolves to its own fixed value
    regardless of the window.
    """
    if isinstance(expr, SelfWindowStart):
        return window.start_ms
    if isinstance(expr, SelfWindowEnd):
        return window.end_ms
    if isinstance(expr, AbsoluteMs):
        return expr.ms
    raise TypeError(f"unhandled TimeExpr variant: {type(expr).__name__}")


@dataclass(frozen=True)
class ResolvedRange:
    """A `RangePattern` resolved against a concrete window.

    `field` names which temporal attribute of a strip element the window
    tests against. `window` is the concrete interval; because it's a
    `TimeRange`, an inverted resolution (start >= end) is rejected at
    construction rather than silently producing an empty match set.
    """

    field: str
    window: TimeRange


def resolve_range(pattern: RangePattern, window: TimeRange) -> ResolvedRange:
    """Resolve a `RangePattern` against the reading node's active window."""
    return ResolvedRange(
        field=pattern.field,
        window=TimeRange(
            start_ms=resolve_time_expr(pattern.start, window),
            end_ms=resolve_time_expr(pattern.end, window),
        ),
    )


def _intersects(window: TimeRange, value: Any) -> bool:
    """True if `value` (a TimeRange-shaped object) intersects `window`.

    Duck-typed on `start_ms` / `end_ms` — the strip element may carry a
    stenota `TimeRange`, which is structurally identical to the nodecules
    one but a distinct type (kept distinct on purpose; see CLAUDE.md).
    """
    return window.start_ms < value.end_ms and value.start_ms < window.end_ms


def range_matches(resolved: ResolvedRange, element: Any) -> bool:
    """True if `element`'s declared temporal field intersects the window.

    The field may be scalar (a single TimeRange — e.g. a claim's
    `source_window`) or list-valued (e.g. a claim's `time_ranges`). A
    list matches iff any of its ranges intersects.

    A field absent on the element raises `AttributeError` — the access
    declaration disagrees with the strip's schema, which is a bug worth
    surfacing loudly rather than silently never-matching.
    """
    value = getattr(element, resolved.field)
    if isinstance(value, (list, tuple)):
        return any(_intersects(resolved.window, v) for v in value)
    return _intersects(resolved.window, value)


__all__ = [
    "ResolvedRange",
    "range_matches",
    "resolve_range",
    "resolve_time_expr",
]
