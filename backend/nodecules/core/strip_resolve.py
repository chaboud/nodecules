"""Resolving declared strip access against a concrete window (PR-r2).

`strip_access.py` is the pure-data ADT — it says *how* a node reads a
strip, symbolically. This module turns a declaration plus the reading
node's concrete active window into something runnable.

Kept separate from the ADT so `strip_access.py` stays dependency-light
(Pydantic only); resolution depends on `core.time.TimeRange`.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Mapping, Optional

from .strip_access import (
    AbsoluteMs,
    Ago,
    RangePattern,
    SelfWindowEnd,
    SelfWindowStart,
    TimeExpr,
)
from .time import TimeRange


def needs_window(expr: TimeExpr) -> bool:
    return isinstance(expr, (SelfWindowStart, SelfWindowEnd)) or (isinstance(expr, Ago) and expr.anchor != "head")


def needs_head(expr: TimeExpr) -> bool:
    return isinstance(expr, Ago) and expr.anchor == "head"


def resolve_time_expr(expr: TimeExpr, window: Optional[TimeRange], head: Optional[int] = None) -> int:
    """Resolve a symbolic `TimeExpr` to an integer ms against `window` (the
    reading node's active window) and `head` (the strip's latest time).

    `SelfWindowStart` / `SelfWindowEnd` resolve to the reading node's
    active window bounds; `AbsoluteMs` resolves to its own fixed value
    regardless of the window; `Ago` looks back from its anchor, never below 0.
    """
    if isinstance(expr, AbsoluteMs):
        return expr.ms
    if needs_window(expr) and window is None:
        raise ValueError("this read is relative to the reader's window: give the node params {'window': {'start_ms', 'end_ms'}}")
    if isinstance(expr, SelfWindowStart):
        return window.start_ms
    if isinstance(expr, SelfWindowEnd):
        return window.end_ms
    if isinstance(expr, Ago):
        if expr.anchor == "head":
            if head is None:
                raise ValueError("a head-relative time needs the strip's head")
            base = head
        else:
            base = window.start_ms if expr.anchor == "self_window_start" else window.end_ms
        return max(0, base - expr.ms)
    raise TypeError(f"unhandled TimeExpr variant: {type(expr).__name__}")


def head_time(elements: Any, field: str) -> Optional[int]:
    """The latest time any element reaches in `field` (its greatest
    `end_ms`), or None for an empty strip. Elements without the field are
    skipped; a strip that never carries it has no head."""
    best: Optional[int] = None
    for el in elements:
        try:
            value = _field(el, field)
        except AttributeError:
            continue
        for v in value if isinstance(value, (list, tuple)) else (value,):
            end = _field(v, "end_ms")
            best = end if best is None or end > best else best
    return best


def earliest_time(elements: Any, field: str) -> Optional[int]:
    """The earliest time any element starts in `field`, or None."""
    best: Optional[int] = None
    for el in elements:
        try:
            value = _field(el, field)
        except AttributeError:
            continue
        for v in value if isinstance(value, (list, tuple)) else (value,):
            start = _field(v, "start_ms")
            best = start if best is None or start < best else best
    return best


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


def resolve_range(pattern: RangePattern, window: Optional[TimeRange], head: Optional[int] = None) -> ResolvedRange:
    """Resolve a `RangePattern` against the reading node's active window and,
    for head-relative bounds, the strip's head."""
    return ResolvedRange(
        field=pattern.field,
        window=TimeRange(
            start_ms=resolve_time_expr(pattern.start, window, head),
            end_ms=resolve_time_expr(pattern.end, window, head),
        ),
    )


def _field(obj: Any, name: str) -> Any:
    """Attribute or mapping key — a strip element may be a model or, once it
    has been through the store, a plain dict. Absence raises loudly."""
    if isinstance(obj, Mapping):
        if name not in obj:
            raise AttributeError(f"element has no field {name!r}")
        return obj[name]
    return getattr(obj, name)


def _intersects(window: TimeRange, value: Any) -> bool:
    """True if `value` (a TimeRange-shaped object or mapping) intersects
    `window`.

    Duck-typed on `start_ms` / `end_ms` — the strip element may carry a
    stenota `TimeRange`, which is structurally identical to the nodecules
    one but a distinct type (kept distinct on purpose; see CLAUDE.md), or a
    `{"start_ms", "end_ms"}` dict from the node store.
    """
    return window.start_ms < _field(value, "end_ms") and _field(value, "start_ms") < window.end_ms


def range_matches(resolved: ResolvedRange, element: Any) -> bool:
    """True if `element`'s declared temporal field intersects the window.

    The field may be scalar (a single TimeRange — e.g. a claim's
    `source_window`) or list-valued (e.g. a claim's `time_ranges`). A
    list matches iff any of its ranges intersects.

    A field absent on the element raises `AttributeError` — the access
    declaration disagrees with the strip's schema, which is a bug worth
    surfacing loudly rather than silently never-matching.
    """
    value = _field(element, resolved.field)
    if isinstance(value, (list, tuple)):
        return any(_intersects(resolved.window, v) for v in value)
    return _intersects(resolved.window, value)


__all__ = [
    "ResolvedRange",
    "earliest_time",
    "head_time",
    "needs_head",
    "needs_window",
    "range_matches",
    "resolve_range",
    "resolve_time_expr",
]
