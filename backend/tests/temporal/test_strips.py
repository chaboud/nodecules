"""Tests for the strip API — StripSpec, StripRegistry, StripView.

These tests use a small Pydantic model defined locally so the tests don't
depend on stenota's schemas. The whole point of the strip API is that it's
duck-typed via `model_validate_json`; any Pydantic class satisfies the
contract.
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest
from pydantic import BaseModel, ConfigDict

from nodecules.core.strips import (
    StripRegistry,
    StripSpec,
    StripView,
)
from nodecules.core.time import TimeRange


# --- Test schemas --------------------------------------------------------


class FakeEvent(BaseModel):
    """Minimal raw-evidence-shaped event: carries a `time_range`."""

    model_config = ConfigDict(frozen=True)

    time_range: TimeRange
    text: str
    kind: str = "evidence"


class FakeClaim(BaseModel):
    """Minimal claim-shaped event: carries a `source_window` instead of `time_range`."""

    model_config = ConfigDict(frozen=True)

    source_window: TimeRange
    claim_text: str
    kind: str = "fact"


# --- Helpers -------------------------------------------------------------


def _write_jsonl(path: Path, records: list[BaseModel]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with open(path, "w", encoding="utf-8") as f:
        for rec in records:
            f.write(rec.model_dump_json() + "\n")


# --- StripSpec / Registry -----------------------------------------------


class TestStripSpec:
    def test_equality_is_structural(self) -> None:
        s1 = StripSpec(name="a", relative_path="x.jsonl", schema_cls=FakeEvent)
        s2 = StripSpec(name="a", relative_path="x.jsonl", schema_cls=FakeEvent)
        assert s1 == s2

    def test_distinct_specs_unequal(self) -> None:
        s1 = StripSpec(name="a", relative_path="x.jsonl", schema_cls=FakeEvent)
        s2 = StripSpec(name="a", relative_path="y.jsonl", schema_cls=FakeEvent)
        assert s1 != s2


class TestStripRegistry:
    def test_register_and_get(self) -> None:
        reg = StripRegistry()
        spec = StripSpec(name="strips/test", relative_path="t.jsonl", schema_cls=FakeEvent)
        reg.register(spec)
        assert reg.has("strips/test")
        assert reg.get("strips/test") == spec

    def test_idempotent_register_is_no_op(self) -> None:
        reg = StripRegistry()
        spec = StripSpec(name="strips/test", relative_path="t.jsonl", schema_cls=FakeEvent)
        reg.register(spec)
        reg.register(spec)  # must not raise
        assert reg.list_names() == ["strips/test"]

    def test_conflicting_register_raises(self) -> None:
        reg = StripRegistry()
        reg.register(
            StripSpec(name="x", relative_path="a.jsonl", schema_cls=FakeEvent)
        )
        with pytest.raises(ValueError, match="already registered"):
            reg.register(
                StripSpec(name="x", relative_path="b.jsonl", schema_cls=FakeEvent)
            )

    def test_get_unknown_raises_with_listing(self) -> None:
        reg = StripRegistry()
        reg.register(
            StripSpec(name="strips/a", relative_path="a.jsonl", schema_cls=FakeEvent)
        )
        with pytest.raises(KeyError, match="strips/a"):
            reg.get("strips/nope")

    def test_unregister_and_clear(self) -> None:
        reg = StripRegistry()
        reg.register(
            StripSpec(name="a", relative_path="a.jsonl", schema_cls=FakeEvent)
        )
        reg.register(
            StripSpec(name="b", relative_path="b.jsonl", schema_cls=FakeEvent)
        )
        reg.unregister("a")
        assert not reg.has("a")
        assert reg.has("b")
        reg.clear()
        assert reg.list_names() == []


# --- StripView iteration -------------------------------------------------


class TestStripViewIteration:
    def test_missing_file_iters_empty(self, tmp_path: Path) -> None:
        spec = StripSpec(
            name="x", relative_path="missing.jsonl", schema_cls=FakeEvent
        )
        view = StripView(spec, tmp_path)
        assert list(view) == []

    def test_iteration_yields_in_arrival_order(self, tmp_path: Path) -> None:
        events = [
            FakeEvent(time_range=TimeRange(start_ms=0, end_ms=1000), text="a"),
            FakeEvent(time_range=TimeRange(start_ms=1000, end_ms=2000), text="b"),
            FakeEvent(time_range=TimeRange(start_ms=2000, end_ms=3000), text="c"),
        ]
        _write_jsonl(tmp_path / "x.jsonl", events)
        view = StripView(
            StripSpec(name="x", relative_path="x.jsonl", schema_cls=FakeEvent),
            tmp_path,
        )
        assert [e.text for e in view] == ["a", "b", "c"]

    def test_blank_lines_are_skipped(self, tmp_path: Path) -> None:
        path = tmp_path / "x.jsonl"
        path.write_text(
            FakeEvent(
                time_range=TimeRange(start_ms=0, end_ms=1), text="a"
            ).model_dump_json()
            + "\n\n"
            + FakeEvent(
                time_range=TimeRange(start_ms=1, end_ms=2), text="b"
            ).model_dump_json()
            + "\n\n\n",
            encoding="utf-8",
        )
        view = StripView(
            StripSpec(name="x", relative_path="x.jsonl", schema_cls=FakeEvent),
            tmp_path,
        )
        assert [e.text for e in view] == ["a", "b"]

    def test_malformed_line_raises(self, tmp_path: Path) -> None:
        path = tmp_path / "x.jsonl"
        path.write_text(
            FakeEvent(
                time_range=TimeRange(start_ms=0, end_ms=1), text="a"
            ).model_dump_json()
            + "\nNOT JSON\n",
            encoding="utf-8",
        )
        view = StripView(
            StripSpec(name="x", relative_path="x.jsonl", schema_cls=FakeEvent),
            tmp_path,
        )
        with pytest.raises(ValueError, match="could not parse"):
            list(view)

    def test_filter_fn_applied(self, tmp_path: Path) -> None:
        events = [
            FakeEvent(time_range=TimeRange(start_ms=0, end_ms=1), text="keep", kind="turn"),
            FakeEvent(time_range=TimeRange(start_ms=1, end_ms=2), text="drop", kind="fact"),
            FakeEvent(time_range=TimeRange(start_ms=2, end_ms=3), text="keep", kind="turn"),
        ]
        _write_jsonl(tmp_path / "x.jsonl", events)
        view = StripView(
            StripSpec(
                name="x",
                relative_path="x.jsonl",
                schema_cls=FakeEvent,
                filter_fn=lambda e: e.kind == "turn",
            ),
            tmp_path,
        )
        assert [e.text for e in view] == ["keep", "keep"]


# --- StripView indexing --------------------------------------------------


class TestStripViewIndexing:
    def _populate(self, tmp_path: Path, n: int = 5) -> StripView:
        events = [
            FakeEvent(
                time_range=TimeRange(start_ms=i * 1000, end_ms=(i + 1) * 1000),
                text=f"e{i}",
            )
            for i in range(n)
        ]
        _write_jsonl(tmp_path / "x.jsonl", events)
        return StripView(
            StripSpec(name="x", relative_path="x.jsonl", schema_cls=FakeEvent),
            tmp_path,
        )

    def test_positive_index(self, tmp_path: Path) -> None:
        view = self._populate(tmp_path)
        assert view[0].text == "e0"
        assert view[3].text == "e3"

    def test_negative_index(self, tmp_path: Path) -> None:
        view = self._populate(tmp_path)
        assert view[-1].text == "e4"
        assert view[-2].text == "e3"

    def test_slice(self, tmp_path: Path) -> None:
        view = self._populate(tmp_path)
        result = view[1:4]
        assert [e.text for e in result] == ["e1", "e2", "e3"]

    def test_at_out_of_range_returns_none(self, tmp_path: Path) -> None:
        view = self._populate(tmp_path)
        assert view.at(0).text == "e0"
        assert view.at(100) is None
        assert view.at(-100) is None

    def test_len(self, tmp_path: Path) -> None:
        view = self._populate(tmp_path, n=7)
        assert len(view) == 7

    def test_latest_on_empty_returns_none(self, tmp_path: Path) -> None:
        view = StripView(
            StripSpec(name="x", relative_path="missing.jsonl", schema_cls=FakeEvent),
            tmp_path,
        )
        assert view.latest() is None

    def test_latest_returns_last(self, tmp_path: Path) -> None:
        view = self._populate(tmp_path)
        assert view.latest().text == "e4"


# --- Time-range queries --------------------------------------------------


class TestStripViewTimeRange:
    def _populate_evidence(self, tmp_path: Path) -> StripView:
        events = [
            FakeEvent(time_range=TimeRange(start_ms=0, end_ms=1_000), text="a"),
            FakeEvent(time_range=TimeRange(start_ms=1_000, end_ms=2_000), text="b"),
            FakeEvent(time_range=TimeRange(start_ms=2_000, end_ms=3_000), text="c"),
            FakeEvent(time_range=TimeRange(start_ms=3_000, end_ms=4_000), text="d"),
        ]
        _write_jsonl(tmp_path / "x.jsonl", events)
        return StripView(
            StripSpec(name="x", relative_path="x.jsonl", schema_cls=FakeEvent),
            tmp_path,
        )

    def test_in_range_intersection(self, tmp_path: Path) -> None:
        view = self._populate_evidence(tmp_path)
        result = list(view.in_range(TimeRange(start_ms=1_500, end_ms=2_500)))
        # Both b ([1000,2000)) and c ([2000,3000)) intersect.
        assert [e.text for e in result] == ["b", "c"]

    def test_in_range_disjoint_is_empty(self, tmp_path: Path) -> None:
        view = self._populate_evidence(tmp_path)
        result = list(view.in_range(TimeRange(start_ms=10_000, end_ms=11_000)))
        assert result == []

    def test_before_returns_most_recent(self, tmp_path: Path) -> None:
        view = self._populate_evidence(tmp_path)
        # Most recent event ending before 2500 is `b` ([1000,2000)).
        result = view.before(TimeRange(start_ms=2_500, end_ms=3_000))
        assert result is not None
        assert result.text == "b"

    def test_before_at_start_returns_none(self, tmp_path: Path) -> None:
        view = self._populate_evidence(tmp_path)
        assert view.before(TimeRange(start_ms=0, end_ms=100)) is None

    def test_after_returns_first(self, tmp_path: Path) -> None:
        view = self._populate_evidence(tmp_path)
        # First event starting at or after 1500 is `c` (start_ms=2000).
        result = view.after(TimeRange(start_ms=1_000, end_ms=1_500))
        assert result is not None
        assert result.text == "c"

    def test_after_at_end_returns_none(self, tmp_path: Path) -> None:
        view = self._populate_evidence(tmp_path)
        assert view.after(TimeRange(start_ms=10_000, end_ms=11_000)) is None

    def test_claim_shape_uses_source_window(self, tmp_path: Path) -> None:
        """Claims have `source_window` not `time_range`; the strip should
        still resolve their time location."""
        claims = [
            FakeClaim(
                source_window=TimeRange(start_ms=0, end_ms=1_000),
                claim_text="hello",
            ),
            FakeClaim(
                source_window=TimeRange(start_ms=1_000, end_ms=2_000),
                claim_text="world",
            ),
        ]
        _write_jsonl(tmp_path / "c.jsonl", claims)
        view = StripView(
            StripSpec(name="c", relative_path="c.jsonl", schema_cls=FakeClaim),
            tmp_path,
        )
        result = list(view.in_range(TimeRange(start_ms=500, end_ms=1_500)))
        assert [c.claim_text for c in result] == ["hello", "world"]
