"""Tests for the execution event schema + sinks."""

from __future__ import annotations

from pathlib import Path

from nodecules.core.events import (
    ExecutionEvent,
    JsonlEventSink,
    ListEventSink,
    NullEventSink,
    read_events,
)
from nodecules.core.time import TimeRange


# --- Schema round-trip --------------------------------------------------


class TestSchema:
    def test_minimal_event(self) -> None:
        e = ExecutionEvent(kind="graph_start")
        assert e.event_id  # auto-populated
        assert e.wall_ts_iso
        assert e.node_id is None

    def test_full_event_round_trip(self) -> None:
        e = ExecutionEvent(
            kind="node_complete",
            meeting_ts_ms=60_000,
            graph_id="g-1",
            execution_id="x-1",
            node_id="summer",
            node_type="test.sum",
            node_version="0.1.0",
            window=TimeRange(start_ms=0, end_ms=60_000),
            cache_key_digest="deadbeef",
            latency_ms=123,
            meta={"custom": "ok"},
        )
        payload = e.model_dump_json()
        loaded = ExecutionEvent.model_validate_json(payload)
        assert loaded == e
        assert loaded.window == TimeRange(start_ms=0, end_ms=60_000)

    def test_invalid_kind_rejected(self) -> None:
        import pydantic

        import pytest
        with pytest.raises(pydantic.ValidationError):
            ExecutionEvent(kind="not_a_kind")  # type: ignore[arg-type]


# --- Sinks ---------------------------------------------------------------


class TestNullSink:
    def test_drops_everything(self) -> None:
        sink = NullEventSink()
        sink.emit(ExecutionEvent(kind="graph_start"))
        sink.close()
        # No-op — nothing to assert beyond "no crash."


class TestListSink:
    def test_collects_events(self) -> None:
        sink = ListEventSink()
        sink.emit(ExecutionEvent(kind="graph_start"))
        sink.emit(ExecutionEvent(kind="node_start", node_id="n1"))
        sink.emit(ExecutionEvent(kind="graph_close"))
        assert len(sink) == 3
        assert [e.kind for e in sink.events] == [
            "graph_start",
            "node_start",
            "graph_close",
        ]

    def test_of_kind_filter(self) -> None:
        sink = ListEventSink()
        sink.emit(ExecutionEvent(kind="node_start", node_id="n1"))
        sink.emit(ExecutionEvent(kind="cache_hit", node_id="n2"))
        sink.emit(ExecutionEvent(kind="node_start", node_id="n3"))
        starts = sink.of_kind("node_start")
        assert {e.node_id for e in starts} == {"n1", "n3"}


class TestJsonlSink:
    def test_writes_valid_jsonl(self, tmp_path: Path) -> None:
        path = tmp_path / "events.jsonl"
        sink = JsonlEventSink(path)
        sink.emit(ExecutionEvent(kind="graph_start", graph_id="g-1"))
        sink.emit(
            ExecutionEvent(
                kind="node_complete",
                node_id="n1",
                latency_ms=42,
                window=TimeRange(start_ms=0, end_ms=1_000),
            )
        )
        sink.close()

        lines = path.read_text().strip().split("\n")
        assert len(lines) == 2

        loaded = read_events(path)
        assert len(loaded) == 2
        assert loaded[0].kind == "graph_start"
        assert loaded[1].kind == "node_complete"
        assert loaded[1].latency_ms == 42
        assert loaded[1].window == TimeRange(start_ms=0, end_ms=1_000)

    def test_append_across_sinks(self, tmp_path: Path) -> None:
        """Two sinks writing to the same path accumulate — important for
        long runs where we want to start a new sink after a graph-close
        without losing prior events."""
        path = tmp_path / "events.jsonl"

        s1 = JsonlEventSink(path)
        s1.emit(ExecutionEvent(kind="graph_start"))
        s1.close()

        s2 = JsonlEventSink(path)
        s2.emit(ExecutionEvent(kind="graph_close"))
        s2.close()

        events = read_events(path)
        assert [e.kind for e in events] == ["graph_start", "graph_close"]


class TestReadEventsErrors:
    def test_malformed_line_raises(self, tmp_path: Path) -> None:
        import pytest

        path = tmp_path / "events.jsonl"
        path.write_text('{"kind": "graph_start"}\nthis is not json\n')
        with pytest.raises(ValueError) as exc_info:
            read_events(path)
        assert str(path) in str(exc_info.value)

    def test_blank_lines_skipped(self, tmp_path: Path) -> None:
        path = tmp_path / "events.jsonl"
        path.write_text(
            '{"kind": "graph_start"}\n\n\n{"kind": "graph_close"}\n'
        )
        events = read_events(path)
        assert [e.kind for e in events] == ["graph_start", "graph_close"]
