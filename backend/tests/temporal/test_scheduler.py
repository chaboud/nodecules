"""Integration tests for `TemporalScheduler.run_batch()`.

These tests exercise the scheduler against a synthetic windowed graph:

    ListInputNode (static)
        └─ values: [ {t, n}, ... ]
            └─> SumInWindowNode (windowed, 1s size, 1s stride)
                    └─ total: sum of n where t ∈ window
                        └─> TotalAcrossWindowsNode (on_graph_close)
                                └─ grand_total: sum of all window totals

All three node types are defined in this file to avoid polluting the
global registry.
"""

from __future__ import annotations

from pathlib import Path
from typing import Any, Dict, List

import pytest

from nodecules.core.annotations import AnnotationIndex, AnnotationNode
from nodecules.core.node_cache import FilesystemNodeCache, InMemoryNodeCache
from nodecules.core.scheduler import TemporalScheduler, compute_windows
from nodecules.core.temporal_context import ChunkedContext
from nodecules.core.time import FileClock, TimeRange
from nodecules.core.types import (
    BaseNode,
    DataType,
    EdgeData,
    ExecutionContext,
    GraphData,
    NodeData,
    NodeSpec,
    ParameterSpec,
    PortSpec,
    WindowSpec,
)


# --- Synthetic node types (defined locally, not registered globally) ----


class ListInputNode(BaseNode):
    """Static source — emits a list of `{t, n}` items from its params."""
    NODE_TYPE = "test.list_input"

    def __init__(self) -> None:
        super().__init__(
            NodeSpec(
                node_type=self.NODE_TYPE,
                display_name="List Input",
                description="static list of {t, n} items",
                inputs=[],
                outputs=[PortSpec(name="items", data_type=DataType.JSON)],
                parameters=[ParameterSpec(name="items", data_type="json", default=[])],
            )
        )

    async def execute(
        self, context: ExecutionContext, node_data: NodeData
    ) -> Dict[str, Any]:
        return {"items": list(node_data.parameters.get("items", []))}


class SumInWindowNode(BaseNode):
    """Windowed summer. Reads `items` and sums `n` for items with t in window."""
    NODE_TYPE = "test.sum_in_window"

    def __init__(self) -> None:
        super().__init__(
            NodeSpec(
                node_type=self.NODE_TYPE,
                display_name="Sum In Window",
                description="sum of n over items whose t falls in the current window",
                inputs=[PortSpec(name="items", data_type=DataType.JSON)],
                outputs=[PortSpec(name="total", data_type=DataType.JSON)],
                temporal_kind="windowed",
                window_spec=WindowSpec(size_ms=1_000, stride_ms=1_000),
                supports_reanneal=True,  # opts into annotation participation
            )
        )

    async def execute(
        self, context: ExecutionContext, node_data: NodeData
    ) -> Dict[str, Any]:
        items = context.get_input_value(node_data.node_id, "items") or []
        if not isinstance(context, ChunkedContext) or context.current_window is None:
            raise RuntimeError("SumInWindowNode requires a ChunkedContext with a window")
        w: TimeRange = context.current_window
        total = sum(item["n"] for item in items if w.contains(int(item["t"])))

        # If an annotation lands in this window, add 100 so tests can
        # tell annotation-driven re-runs apart from cache hits.
        ann_refs = context.get_annotations_in_window(w)
        if ann_refs:
            total += 100

        return {"total": total}


class TotalAcrossWindowsNode(BaseNode):
    """on_graph_close reducer. Sums the list of per-window totals."""
    NODE_TYPE = "test.total_across_windows"

    def __init__(self) -> None:
        super().__init__(
            NodeSpec(
                node_type=self.NODE_TYPE,
                display_name="Total Across Windows",
                description="sums per-window totals at graph close",
                inputs=[PortSpec(name="per_window_totals", data_type=DataType.JSON)],
                outputs=[PortSpec(name="grand_total", data_type=DataType.JSON)],
                emit_policy="on_graph_close",
            )
        )

    async def execute(
        self, context: ExecutionContext, node_data: NodeData
    ) -> Dict[str, Any]:
        totals = context.get_input_value(node_data.node_id, "per_window_totals") or []
        return {"grand_total": sum(v for v in totals if v is not None)}


# --- Helpers --------------------------------------------------------------


NODE_REGISTRY = {
    ListInputNode.NODE_TYPE: ListInputNode,
    SumInWindowNode.NODE_TYPE: SumInWindowNode,
    TotalAcrossWindowsNode.NODE_TYPE: TotalAcrossWindowsNode,
}


def make_graph(items: List[Dict[str, int]]) -> GraphData:
    """Build the standard test graph:  input → summer → reducer."""
    src = NodeData(
        node_id="src",
        node_type=ListInputNode.NODE_TYPE,
        parameters={"items": items},
    )
    summer = NodeData(
        node_id="summer",
        node_type=SumInWindowNode.NODE_TYPE,
    )
    reducer = NodeData(
        node_id="reducer",
        node_type=TotalAcrossWindowsNode.NODE_TYPE,
    )
    return GraphData(
        graph_id="test-graph",
        name="test",
        nodes={"src": src, "summer": summer, "reducer": reducer},
        edges=[
            EdgeData(
                edge_id="e1",
                source_node="src",
                source_port="items",
                target_node="summer",
                target_port="items",
            ),
            EdgeData(
                edge_id="e2",
                source_node="summer",
                source_port="total",
                target_node="reducer",
                target_port="per_window_totals",
            ),
        ],
    )


# --- compute_windows -----------------------------------------------------


class TestComputeWindows:
    def test_non_overlapping(self) -> None:
        spec = WindowSpec(size_ms=1_000, stride_ms=1_000)
        windows = compute_windows(spec, TimeRange(start_ms=0, end_ms=4_000))
        assert [w.start_ms for w in windows] == [0, 1_000, 2_000, 3_000]
        assert all(w.duration_ms() == 1_000 for w in windows)

    def test_overlapping_stride_smaller_than_size(self) -> None:
        spec = WindowSpec(size_ms=1_000, stride_ms=500)
        windows = compute_windows(spec, TimeRange(start_ms=0, end_ms=2_000))
        assert [w.start_ms for w in windows] == [0, 500, 1_000, 1_500]

    def test_origin_alignment_snaps_to_stride_grid(self) -> None:
        spec = WindowSpec(size_ms=1_000, stride_ms=1_000, align="origin")
        windows = compute_windows(spec, TimeRange(start_ms=2_500, end_ms=4_000))
        # k=2 gives start=2000, k=3 gives 3000 — 2000 is the last stride
        # grid point not past start_ms.
        assert windows[0].start_ms == 2_000

    def test_boundary_alignment_anchors_at_range_start(self) -> None:
        spec = WindowSpec(size_ms=1_000, stride_ms=1_000, align="boundary")
        windows = compute_windows(spec, TimeRange(start_ms=2_500, end_ms=4_000))
        assert windows[0].start_ms == 2_500


# --- End-to-end batch ----------------------------------------------------


class TestBatchRun:
    async def test_three_window_batch(self) -> None:
        items = [
            {"t": 100, "n": 1},
            {"t": 500, "n": 2},
            # window 1
            {"t": 1_200, "n": 10},
            {"t": 1_800, "n": 20},
            # window 2
            {"t": 2_100, "n": 100},
        ]
        graph = make_graph(items)
        sched = TemporalScheduler(NODE_REGISTRY, time_source=FileClock())
        ctx = await sched.run_batch(graph, total_duration_ms=3_000)

        # Per-window consolidation visible to the reducer node.
        totals = ctx.node_outputs["summer"]["total"]
        assert totals == [3, 30, 100]
        assert ctx.node_outputs["reducer"]["grand_total"] == 133

    async def test_empty_window_still_runs(self) -> None:
        """A window with no matching items produces a valid zero output, not a skip."""
        items = [{"t": 100, "n": 5}, {"t": 2_100, "n": 5}]
        graph = make_graph(items)
        sched = TemporalScheduler(NODE_REGISTRY, time_source=FileClock())
        ctx = await sched.run_batch(graph, total_duration_ms=3_000)
        assert ctx.node_outputs["summer"]["total"] == [5, 0, 5]

    async def test_file_clock_reaches_end(self) -> None:
        """FileClock is advanced to total_duration_ms and marked closed."""
        items = [{"t": 100, "n": 1}]
        graph = make_graph(items)
        clock = FileClock()
        sched = TemporalScheduler(NODE_REGISTRY, time_source=clock)
        await sched.run_batch(graph, total_duration_ms=2_000)
        assert await clock.now_ms() == 2_000
        assert clock.is_closed()


# --- Cache ----------------------------------------------------------------


class TestCaching:
    async def test_rerun_with_same_cache_is_a_hit(self) -> None:
        """Second run with the same cache does not re-execute windowed nodes."""
        items = [{"t": 100, "n": 1}, {"t": 1_100, "n": 10}]
        cache = InMemoryNodeCache()
        graph = make_graph(items)

        s1 = TemporalScheduler(NODE_REGISTRY, time_source=FileClock(), cache=cache)
        await s1.run_batch(graph, total_duration_ms=2_000)
        first_len = len(cache)

        # A hit-counting wrapper would be cleaner but requires more
        # machinery — the observable signal is that the cache already
        # has an entry and run_batch still succeeds.
        s2 = TemporalScheduler(NODE_REGISTRY, time_source=FileClock(), cache=cache)
        ctx2 = await s2.run_batch(graph, total_duration_ms=2_000)

        # Same outputs on the second run.
        assert ctx2.node_outputs["summer"]["total"] == [1, 10]
        # No new cache entries written.
        assert len(cache) == first_len

    async def test_filesystem_cache_persists_across_runs(self, tmp_path: Path) -> None:
        items = [{"t": 100, "n": 7}]
        graph = make_graph(items)

        cache1 = FilesystemNodeCache(tmp_path / "cache")
        sched1 = TemporalScheduler(NODE_REGISTRY, time_source=FileClock(), cache=cache1)
        await sched1.run_batch(graph, total_duration_ms=1_000)

        # Fresh scheduler + fresh cache handle pointing at the same dir.
        cache2 = FilesystemNodeCache(tmp_path / "cache")
        sched2 = TemporalScheduler(NODE_REGISTRY, time_source=FileClock(), cache=cache2)
        ctx = await sched2.run_batch(graph, total_duration_ms=1_000)
        assert ctx.node_outputs["summer"]["total"] == [7]
        # At least one cache file exists.
        assert any((tmp_path / "cache").glob("*.json"))


# --- Annotation invalidation ---------------------------------------------


class TestAnnotationInvalidation:
    async def test_annotation_reruns_only_affected_window(self) -> None:
        """An annotation in window 1 triggers a +100 recompute there and
        leaves windows 0 and 2 cached (same totals as before)."""
        items = [
            {"t": 100, "n": 1},
            {"t": 1_100, "n": 10},
            {"t": 2_100, "n": 100},
        ]
        cache = InMemoryNodeCache()
        annotations = AnnotationIndex()
        graph = make_graph(items)

        s1 = TemporalScheduler(
            NODE_REGISTRY,
            time_source=FileClock(),
            cache=cache,
            annotation_index=annotations,
        )
        ctx1 = await s1.run_batch(graph, total_duration_ms=3_000)
        assert ctx1.node_outputs["summer"]["total"] == [1, 10, 100]

        # Annotation targeting window 1.
        annotations.add(
            AnnotationNode(
                node_id="ann-1",
                emission_ms=1_500,
                target_window=TimeRange(start_ms=1_200, end_ms=1_800),
                payload={"note": "correction"},
            )
        )

        s2 = TemporalScheduler(
            NODE_REGISTRY,
            time_source=FileClock(),
            cache=cache,
            annotation_index=annotations,
        )
        ctx2 = await s2.run_batch(graph, total_duration_ms=3_000)
        # Windows 0 and 2 unaffected; window 1 gets the +100 from the
        # annotation branch.
        assert ctx2.node_outputs["summer"]["total"] == [1, 110, 100]
        # Reducer picks up the new per-window total.
        assert ctx2.node_outputs["reducer"]["grand_total"] == 211


# --- Streaming rejection -------------------------------------------------


class TestStreamingRejected:
    async def test_streaming_node_fails_at_run(self) -> None:
        """PR-n3 scope: streaming nodes raise NotImplementedError."""

        class StreamingNode(BaseNode):
            NODE_TYPE = "test.streaming"

            def __init__(self) -> None:
                super().__init__(
                    NodeSpec(
                        node_type=self.NODE_TYPE,
                        display_name="Streaming",
                        description="",
                        temporal_kind="streaming",
                        emit_policy="streaming",
                    )
                )

            async def execute(self, context, node_data):
                return {}

        graph = GraphData(
            graph_id="s",
            nodes={
                "s1": NodeData(node_id="s1", node_type=StreamingNode.NODE_TYPE),
            },
        )
        sched = TemporalScheduler(
            {**NODE_REGISTRY, StreamingNode.NODE_TYPE: StreamingNode},
            time_source=FileClock(),
        )
        with pytest.raises(NotImplementedError):
            await sched.run_batch(graph, total_duration_ms=1_000)


# --- Static-only regression ----------------------------------------------


class TestStaticRegression:
    async def test_pure_static_graph_unchanged(self) -> None:
        """A graph with only static nodes runs under the scheduler
        identically to the base executor — no windowing, no cache keys
        with `window_hash`, nothing temporal touches it."""

        class IdentityNode(BaseNode):
            NODE_TYPE = "test.identity"

            def __init__(self) -> None:
                super().__init__(
                    NodeSpec(
                        node_type=self.NODE_TYPE,
                        display_name="Identity",
                        description="",
                        inputs=[PortSpec(name="input", data_type=DataType.JSON)],
                        outputs=[PortSpec(name="output", data_type=DataType.JSON)],
                    )
                )

            async def execute(self, context, node_data):
                return {
                    "output": context.get_input_value(node_data.node_id, "input"),
                }

        graph = GraphData(
            graph_id="g",
            nodes={
                "src": NodeData(
                    node_id="src",
                    node_type=ListInputNode.NODE_TYPE,
                    parameters={"items": [{"t": 1, "n": 42}]},
                ),
                "pass": NodeData(node_id="pass", node_type=IdentityNode.NODE_TYPE),
            },
            edges=[
                EdgeData(
                    edge_id="e",
                    source_node="src",
                    source_port="items",
                    target_node="pass",
                    target_port="input",
                ),
            ],
        )
        registry = {
            ListInputNode.NODE_TYPE: ListInputNode,
            IdentityNode.NODE_TYPE: IdentityNode,
        }
        sched = TemporalScheduler(registry, time_source=FileClock())
        ctx = await sched.run_batch(graph, total_duration_ms=1_000)
        assert ctx.node_outputs["pass"]["output"] == [{"t": 1, "n": 42}]
