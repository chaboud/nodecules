"""Tests for PR-r2 — resolving declared strip access against a window.

The ADT (`strip_access.py`) is pure data: it says *how* a node reads a
strip symbolically. The resolver turns a declaration plus the reading
node's concrete active window into something runnable:

- `resolve_time_expr` — a `TimeExpr` against a window → integer ms.
- `resolve_range`     — a `RangePattern` against a window → a concrete
                        `ResolvedRange(field, window)`.
- `range_matches`     — a `ResolvedRange` against a strip element → bool.

Grounded against `SummarizerL2Node`, whose hand-written filter is
`c.source_window.intersects(window)` — exactly what a resolved
`RangePattern(field="source_window", ...)` reproduces.
"""

from __future__ import annotations

import pytest

from nodecules.core.strip_access import (
    AbsoluteMs,
    RangePattern,
    SelfWindowEnd,
    SelfWindowStart,
)
from nodecules.core.strip_resolve import (
    ResolvedRange,
    range_matches,
    resolve_range,
    resolve_time_expr,
)
from nodecules.core.time import TimeRange


class _Element:
    """Lightweight strip-element stand-in.

    The match predicate duck-types — it reads the named field and tests
    intersection. A full `StructuredClaim` would drag in stenota; this
    carries just the temporal field under test, with real `TimeRange`s.
    """

    def __init__(self, **fields: object) -> None:
        for name, value in fields.items():
            setattr(self, name, value)


class TestResolveTimeExpr:
    def test_self_window_start_resolves_to_window_start(self) -> None:
        window = TimeRange(start_ms=30_000, end_ms=120_000)
        assert resolve_time_expr(SelfWindowStart(), window) == 30_000

    def test_self_window_end_resolves_to_window_end(self) -> None:
        window = TimeRange(start_ms=30_000, end_ms=120_000)
        assert resolve_time_expr(SelfWindowEnd(), window) == 120_000

    def test_absolute_ms_resolves_to_its_value(self) -> None:
        window = TimeRange(start_ms=30_000, end_ms=120_000)
        assert resolve_time_expr(AbsoluteMs(ms=5_000), window) == 5_000

    def test_absolute_ms_ignores_the_window(self) -> None:
        # An absolute timestamp doesn't depend on which window is reading.
        w1 = TimeRange(start_ms=0, end_ms=1_000)
        w2 = TimeRange(start_ms=500_000, end_ms=600_000)
        assert resolve_time_expr(AbsoluteMs(ms=42), w1) == 42
        assert resolve_time_expr(AbsoluteMs(ms=42), w2) == 42


class TestResolveRange:
    def test_self_window_range_resolves_to_the_active_window(self) -> None:
        # SummarizerL2's real declaration: Range over the active window.
        pattern = RangePattern(
            field="source_window",
            start=SelfWindowStart(),
            end=SelfWindowEnd(),
        )
        window = TimeRange(start_ms=0, end_ms=90_000)
        resolved = resolve_range(pattern, window)
        assert isinstance(resolved, ResolvedRange)
        assert resolved.field == "source_window"
        assert resolved.window == TimeRange(start_ms=0, end_ms=90_000)

    def test_absolute_endpoints_resolve_independent_of_window(self) -> None:
        pattern = RangePattern(
            field="time_ranges",
            start=AbsoluteMs(ms=10_000),
            end=AbsoluteMs(ms=20_000),
        )
        resolved = resolve_range(pattern, TimeRange(start_ms=0, end_ms=500))
        assert resolved.window == TimeRange(start_ms=10_000, end_ms=20_000)

    def test_inverted_resolution_is_rejected(self) -> None:
        # start=SelfWindowEnd, end=SelfWindowStart resolves to an inverted
        # range — TimeRange construction must reject it loudly rather than
        # silently producing an empty / negative window.
        pattern = RangePattern(
            field="source_window",
            start=SelfWindowEnd(),
            end=SelfWindowStart(),
        )
        with pytest.raises(ValueError):
            resolve_range(pattern, TimeRange(start_ms=0, end_ms=90_000))


class TestRangeMatchesScalarField:
    """A scalar temporal field (e.g. a claim's `source_window`)."""

    _resolved = ResolvedRange(
        field="source_window", window=TimeRange(start_ms=0, end_ms=90_000)
    )

    def test_overlapping_scalar_matches(self) -> None:
        el = _Element(source_window=TimeRange(start_ms=40_000, end_ms=100_000))
        assert range_matches(self._resolved, el) is True

    def test_disjoint_scalar_does_not_match(self) -> None:
        el = _Element(source_window=TimeRange(start_ms=200_000, end_ms=300_000))
        assert range_matches(self._resolved, el) is False

    def test_touching_scalar_does_not_match(self) -> None:
        # Closed-open: [0,90_000) and [90_000,...) do not intersect.
        el = _Element(source_window=TimeRange(start_ms=90_000, end_ms=120_000))
        assert range_matches(self._resolved, el) is False


class TestRangeMatchesListField:
    """A list-valued temporal field (e.g. a claim's `time_ranges`) — the
    element matches iff ANY of its ranges intersects the window."""

    _resolved = ResolvedRange(
        field="time_ranges", window=TimeRange(start_ms=0, end_ms=90_000)
    )

    def test_any_overlapping_range_matches(self) -> None:
        el = _Element(
            time_ranges=[
                TimeRange(start_ms=500_000, end_ms=600_000),
                TimeRange(start_ms=10_000, end_ms=20_000),
            ]
        )
        assert range_matches(self._resolved, el) is True

    def test_no_overlapping_range_does_not_match(self) -> None:
        el = _Element(
            time_ranges=[
                TimeRange(start_ms=500_000, end_ms=600_000),
                TimeRange(start_ms=700_000, end_ms=800_000),
            ]
        )
        assert range_matches(self._resolved, el) is False

    def test_empty_list_does_not_match(self) -> None:
        assert range_matches(self._resolved, _Element(time_ranges=[])) is False


class TestRangeMatchesMissingField:
    def test_missing_declared_field_raises(self) -> None:
        # A declared field absent on the element means the access
        # declaration disagrees with the strip's schema — fail loud.
        resolved = ResolvedRange(
            field="source_window", window=TimeRange(start_ms=0, end_ms=90_000)
        )
        with pytest.raises(AttributeError):
            range_matches(resolved, _Element(time_ranges=[]))
