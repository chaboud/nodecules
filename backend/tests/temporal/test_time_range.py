"""Unit tests for `TimeRange` primitive semantics."""

from __future__ import annotations

import pytest
from pydantic import ValidationError

from nodecules.core.time import TimeRange


class TestConstruction:
    def test_basic_valid_range(self) -> None:
        r = TimeRange(start_ms=0, end_ms=1000)
        assert r.start_ms == 0
        assert r.end_ms == 1000
        assert r.duration_ms() == 1000

    def test_zero_duration_rejected(self) -> None:
        with pytest.raises(ValidationError):
            TimeRange(start_ms=500, end_ms=500)

    def test_inverted_range_rejected(self) -> None:
        with pytest.raises(ValidationError):
            TimeRange(start_ms=1000, end_ms=500)

    def test_negative_start_rejected(self) -> None:
        with pytest.raises(ValidationError):
            TimeRange(start_ms=-1, end_ms=100)

    def test_frozen_is_immutable(self) -> None:
        r = TimeRange(start_ms=0, end_ms=100)
        with pytest.raises(ValidationError):
            r.start_ms = 50  # type: ignore[misc]


class TestContains:
    def test_contains_start_boundary_is_inclusive(self) -> None:
        r = TimeRange(start_ms=100, end_ms=200)
        assert r.contains(100) is True

    def test_contains_end_boundary_is_exclusive(self) -> None:
        r = TimeRange(start_ms=100, end_ms=200)
        assert r.contains(200) is False

    def test_contains_interior(self) -> None:
        r = TimeRange(start_ms=100, end_ms=200)
        assert r.contains(150) is True

    def test_contains_outside(self) -> None:
        r = TimeRange(start_ms=100, end_ms=200)
        assert r.contains(50) is False
        assert r.contains(300) is False


class TestIntersects:
    def test_overlapping_ranges_intersect(self) -> None:
        assert TimeRange(start_ms=0, end_ms=100).intersects(
            TimeRange(start_ms=50, end_ms=150)
        )

    def test_touching_ranges_do_not_intersect(self) -> None:
        # Closed-open semantics: [0, 100) and [100, 200) share no points.
        assert (
            TimeRange(start_ms=0, end_ms=100).intersects(
                TimeRange(start_ms=100, end_ms=200)
            )
            is False
        )

    def test_disjoint_ranges_do_not_intersect(self) -> None:
        assert (
            TimeRange(start_ms=0, end_ms=50).intersects(
                TimeRange(start_ms=100, end_ms=150)
            )
            is False
        )

    def test_fully_contained_ranges_intersect(self) -> None:
        assert TimeRange(start_ms=0, end_ms=1000).intersects(
            TimeRange(start_ms=400, end_ms=500)
        )

    def test_intersection_is_symmetric(self) -> None:
        a = TimeRange(start_ms=0, end_ms=100)
        b = TimeRange(start_ms=50, end_ms=150)
        assert a.intersects(b) == b.intersects(a)


class TestIntersection:
    def test_overlapping_intersection(self) -> None:
        a = TimeRange(start_ms=0, end_ms=100)
        b = TimeRange(start_ms=50, end_ms=150)
        assert a.intersection(b) == TimeRange(start_ms=50, end_ms=100)

    def test_disjoint_intersection_is_none(self) -> None:
        a = TimeRange(start_ms=0, end_ms=100)
        b = TimeRange(start_ms=200, end_ms=300)
        assert a.intersection(b) is None

    def test_touching_intersection_is_none(self) -> None:
        # [0, 100) ∩ [100, 200) = ∅
        a = TimeRange(start_ms=0, end_ms=100)
        b = TimeRange(start_ms=100, end_ms=200)
        assert a.intersection(b) is None

    def test_nested_intersection_is_inner(self) -> None:
        outer = TimeRange(start_ms=0, end_ms=1000)
        inner = TimeRange(start_ms=400, end_ms=500)
        assert outer.intersection(inner) == inner


class TestUnion:
    def test_overlapping_union(self) -> None:
        a = TimeRange(start_ms=0, end_ms=100)
        b = TimeRange(start_ms=50, end_ms=150)
        assert a.union(b) == TimeRange(start_ms=0, end_ms=150)

    def test_disjoint_union_bridges_gap(self) -> None:
        # Documented behavior — callers wanting to reject disjoint unions
        # check `intersects` first.
        a = TimeRange(start_ms=0, end_ms=50)
        b = TimeRange(start_ms=100, end_ms=150)
        assert a.union(b) == TimeRange(start_ms=0, end_ms=150)

    def test_nested_union_is_outer(self) -> None:
        outer = TimeRange(start_ms=0, end_ms=1000)
        inner = TimeRange(start_ms=400, end_ms=500)
        assert outer.union(inner) == outer


class TestShift:
    def test_positive_shift(self) -> None:
        r = TimeRange(start_ms=100, end_ms=200)
        assert r.shift(50) == TimeRange(start_ms=150, end_ms=250)

    def test_negative_shift(self) -> None:
        r = TimeRange(start_ms=100, end_ms=200)
        assert r.shift(-50) == TimeRange(start_ms=50, end_ms=150)

    def test_shift_going_negative_is_rejected(self) -> None:
        r = TimeRange(start_ms=100, end_ms=200)
        with pytest.raises(ValueError):
            r.shift(-200)


class TestDuration:
    def test_duration_basic(self) -> None:
        assert TimeRange(start_ms=0, end_ms=500).duration_ms() == 500

    def test_duration_offset(self) -> None:
        assert TimeRange(start_ms=12345, end_ms=13345).duration_ms() == 1000


class TestEquality:
    def test_equal_ranges_compare_equal(self) -> None:
        assert TimeRange(start_ms=0, end_ms=100) == TimeRange(start_ms=0, end_ms=100)

    def test_ranges_are_hashable(self) -> None:
        s = {TimeRange(start_ms=0, end_ms=100), TimeRange(start_ms=0, end_ms=100)}
        assert len(s) == 1
