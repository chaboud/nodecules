"""Scheduler + event-log integration tests.

Reuse the synthetic graph from `test_scheduler.py`. The assertions here
are about which events fire in what order, NOT about what results they
compute (that's covered in `test_scheduler.py`).
"""

from __future__ import annotations

from pathlib import Path

from nodecules.core.annotations import AnnotationIndex, AnnotationNode
from nodecules.core.events import (
    JsonlEventSink,
    ListEventSink,
    read_events,
)
from nodecules.core.node_cache import InMemoryNodeCache
from nodecules.core.scheduler import TemporalScheduler
from nodecules.core.time import FileClock, TimeRange

from .test_scheduler import NODE_REGISTRY, make_graph


class TestSchedulerEmitsEvents:
    async def test_graph_start_and_close_bookend_the_run(self) -> None:
        sink = ListEventSink()
        graph = make_graph([{"t": 100, "n": 1}])
        sched = TemporalScheduler(
            NODE_REGISTRY, time_source=FileClock(), event_sink=sink
        )
        await sched.run_batch(graph, total_duration_ms=1_000)

        kinds = [e.kind for e in sink.events]
        assert kinds[0] == "graph_start"
        assert kinds[-1] == "graph_close"

    async def test_every_windowed_execution_emits_start_and_complete(self) -> None:
        """Three windows → three (node_start, node_complete, window_emit) triples
        for the summer node."""
        sink = ListEventSink()
        graph = make_graph([{"t": 100, "n": 1}, {"t": 1_100, "n": 10}])
        sched = TemporalScheduler(
            NODE_REGISTRY, time_source=FileClock(), event_sink=sink
        )
        await sched.run_batch(graph, total_duration_ms=2_000)

        summer_starts = [
            e for e in sink.events if e.kind == "node_start" and e.node_id == "summer"
        ]
        summer_completes = [
            e
            for e in sink.events
            if e.kind == "node_complete" and e.node_id == "summer"
        ]
        summer_emits = [
            e
            for e in sink.events
            if e.kind == "window_emit" and e.node_id == "summer"
        ]
        assert len(summer_starts) == 2
        assert len(summer_completes) == 2
        assert len(summer_emits) == 2

        # Each summer event carries the window it applied to.
        windows = {e.window for e in summer_starts}
        assert windows == {
            TimeRange(start_ms=0, end_ms=1_000),
            TimeRange(start_ms=1_000, end_ms=2_000),
        }

    async def test_cache_hits_emit_cache_hit_not_node_complete(self) -> None:
        """On re-run with a warm cache, summer gets cache_hit events, not
        node_start/complete."""
        cache = InMemoryNodeCache()
        graph = make_graph([{"t": 100, "n": 1}, {"t": 1_100, "n": 10}])

        # Warm the cache.
        warm = ListEventSink()
        s1 = TemporalScheduler(
            NODE_REGISTRY,
            time_source=FileClock(),
            cache=cache,
            event_sink=warm,
        )
        await s1.run_batch(graph, total_duration_ms=2_000)

        # Re-run with a fresh sink.
        sink = ListEventSink()
        s2 = TemporalScheduler(
            NODE_REGISTRY,
            time_source=FileClock(),
            cache=cache,
            event_sink=sink,
        )
        await s2.run_batch(graph, total_duration_ms=2_000)

        summer_hits = [
            e for e in sink.events if e.kind == "cache_hit" and e.node_id == "summer"
        ]
        summer_completes = [
            e
            for e in sink.events
            if e.kind == "node_complete" and e.node_id == "summer"
        ]
        assert len(summer_hits) == 2
        assert len(summer_completes) == 0

    async def test_on_graph_close_node_fires_last(self) -> None:
        """The reducer is on_graph_close — its complete event must come
        after every summer complete."""
        sink = ListEventSink()
        graph = make_graph([{"t": 100, "n": 1}, {"t": 1_100, "n": 10}])
        sched = TemporalScheduler(
            NODE_REGISTRY, time_source=FileClock(), event_sink=sink
        )
        await sched.run_batch(graph, total_duration_ms=2_000)

        kinds_for_reducer = [
            (i, e)
            for i, e in enumerate(sink.events)
            if e.node_id == "reducer" and e.kind == "node_complete"
        ]
        kinds_for_summer_completes = [
            i
            for i, e in enumerate(sink.events)
            if e.node_id == "summer" and e.kind == "node_complete"
        ]
        assert kinds_for_reducer, "reducer should have completed"
        reducer_complete_idx = kinds_for_reducer[0][0]
        assert all(
            i < reducer_complete_idx for i in kinds_for_summer_completes
        )

    async def test_annotation_invalidation_emits_mix_of_hits_and_runs(
        self,
    ) -> None:
        """After an annotation lands in window 1, a re-run emits cache_hit
        for windows 0 and 2, and node_start+node_complete for window 1."""
        cache = InMemoryNodeCache()
        annotations = AnnotationIndex()
        graph = make_graph(
            [{"t": 100, "n": 1}, {"t": 1_100, "n": 10}, {"t": 2_100, "n": 100}]
        )

        warm = ListEventSink()
        s1 = TemporalScheduler(
            NODE_REGISTRY,
            time_source=FileClock(),
            cache=cache,
            annotation_index=annotations,
            event_sink=warm,
        )
        await s1.run_batch(graph, total_duration_ms=3_000)

        annotations.add(
            AnnotationNode(
                node_id="ann-1",
                emission_ms=1_500,
                target_window=TimeRange(start_ms=1_200, end_ms=1_800),
                payload={"note": "correction"},
            )
        )

        sink = ListEventSink()
        s2 = TemporalScheduler(
            NODE_REGISTRY,
            time_source=FileClock(),
            cache=cache,
            annotation_index=annotations,
            event_sink=sink,
        )
        await s2.run_batch(graph, total_duration_ms=3_000)

        summer_hits = [
            e for e in sink.events if e.kind == "cache_hit" and e.node_id == "summer"
        ]
        summer_runs = [
            e
            for e in sink.events
            if e.kind == "node_complete" and e.node_id == "summer"
        ]
        assert len(summer_hits) == 2
        assert len(summer_runs) == 1
        # The re-run event applies to the middle window.
        assert summer_runs[0].window == TimeRange(start_ms=1_000, end_ms=2_000)


class TestJsonlEventSinkIntegration:
    async def test_real_run_writes_readable_event_log(self, tmp_path: Path) -> None:
        path = tmp_path / "events.jsonl"
        sink = JsonlEventSink(path)
        graph = make_graph([{"t": 100, "n": 1}, {"t": 1_100, "n": 10}])
        sched = TemporalScheduler(
            NODE_REGISTRY, time_source=FileClock(), event_sink=sink
        )
        await sched.run_batch(graph, total_duration_ms=2_000)

        events = read_events(path)
        assert events[0].kind == "graph_start"
        assert events[-1].kind == "graph_close"
        # Every window_emit event carries its window.
        window_emits = [e for e in events if e.kind == "window_emit"]
        assert all(e.window is not None for e in window_emits)
        # Latency ms populated on node_complete events.
        completes = [e for e in events if e.kind == "node_complete"]
        assert all(e.latency_ms is not None for e in completes)
