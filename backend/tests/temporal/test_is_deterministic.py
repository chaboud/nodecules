"""Tests for is_deterministic routing through TemporalScheduler (PR-n4).

A windowed node spec declaring `is_deterministic=False` should cause its
cache entry to be flagged so the eviction policy pins it. The default is
True, preserving pre-PR-n4 behavior — every existing test in this
directory uses default specs and continues to pass unchanged.

Nondeterministic nodes also still produce cache hits on re-run (the cache
is still storing their outputs); the difference is purely in eviction
eligibility.
"""

from __future__ import annotations

import json
import time
from pathlib import Path
from typing import Any, Dict

import pytest

from nodecules.core.node_cache import FilesystemNodeCache, InMemoryNodeCache
from nodecules.core.scheduler import TemporalScheduler
from nodecules.core.temporal_context import ChunkedContext
from nodecules.core.time import FileClock, TimeRange
from nodecules.core.types import (
    BaseNode,
    DataType,
    GraphData,
    NodeData,
    NodeSpec,
    PortSpec,
    WindowSpec,
)


# --- Synthetic node types ------------------------------------------------


class _WindowStartReporter(BaseNode):
    """Base class — reports `current_window.start_ms` as its output.

    Subclasses differ only in `is_deterministic`. This is what we're
    testing: the same data flow, two different determinism declarations,
    distinguishable in the cache layer.
    """

    IS_DETERMINISTIC: bool = True

    def __init__(self) -> None:
        super().__init__(
            NodeSpec(
                node_type=type(self).NODE_TYPE,
                display_name=type(self).NODE_TYPE,
                description="",
                inputs=[],
                outputs=[PortSpec(name="value", data_type=DataType.JSON)],
                temporal_kind="windowed",
                window_spec=WindowSpec(size_ms=1_000, stride_ms=1_000),
                is_deterministic=type(self).IS_DETERMINISTIC,
            )
        )

    async def execute(self, context: Any, node_data: NodeData) -> Dict[str, Any]:
        if not isinstance(context, ChunkedContext) or context.current_window is None:
            return {"value": -1}
        return {"value": context.current_window.start_ms}


class DetReporter(_WindowStartReporter):
    NODE_TYPE = "test.det_reporter"
    IS_DETERMINISTIC = True


class NondetReporter(_WindowStartReporter):
    NODE_TYPE = "test.nondet_reporter"
    IS_DETERMINISTIC = False


REGISTRY = {
    DetReporter.NODE_TYPE: DetReporter,
    NondetReporter.NODE_TYPE: NondetReporter,
}


def _single_node_graph(node_type: str) -> GraphData:
    return GraphData(
        graph_id="g",
        nodes={"reporter": NodeData(node_id="reporter", node_type=node_type)},
    )


# --- Tests ---------------------------------------------------------------


class TestSpecDefault:
    def test_nodespec_default_is_deterministic_true(self) -> None:
        """Pre-PR-n4 plugins (no explicit flag) must continue caching as
        deterministic. Verified at the type level so a regression here
        would be a static-DAG breakage."""
        spec = NodeSpec(node_type="x", display_name="x", description="")
        assert spec.is_deterministic is True


class TestRouting:
    async def test_deterministic_entry_flag_persists(self, tmp_path: Path) -> None:
        cache = FilesystemNodeCache(tmp_path / "cache")
        sched = TemporalScheduler(
            REGISTRY, cache=cache, time_source=FileClock()
        )
        await sched.run_batch(
            _single_node_graph(DetReporter.NODE_TYPE),
            total_duration_ms=3_000,
        )
        files = list((tmp_path / "cache").glob("*.json"))
        assert len(files) == 3  # one entry per window
        for f in files:
            blob = json.loads(f.read_text())
            assert blob["is_deterministic"] is True

    async def test_nondeterministic_entry_flag_persists(self, tmp_path: Path) -> None:
        cache = FilesystemNodeCache(tmp_path / "cache")
        sched = TemporalScheduler(
            REGISTRY, cache=cache, time_source=FileClock()
        )
        await sched.run_batch(
            _single_node_graph(NondetReporter.NODE_TYPE),
            total_duration_ms=3_000,
        )
        files = list((tmp_path / "cache").glob("*.json"))
        assert len(files) == 3
        for f in files:
            blob = json.loads(f.read_text())
            assert blob["is_deterministic"] is False

    async def test_nondeterministic_entries_survive_eviction_pressure(self) -> None:
        """Three windows of a nondeterministic node + a tight cap should
        keep all three entries alive (pinned), not evict any."""
        cache = InMemoryNodeCache(max_entries=1)
        sched = TemporalScheduler(
            REGISTRY, cache=cache, time_source=FileClock()
        )
        await sched.run_batch(
            _single_node_graph(NondetReporter.NODE_TYPE),
            total_duration_ms=3_000,
        )
        # All three entries are pinned; cap of 1 is exceeded but cache
        # works safely.
        assert len(cache) == 3

    async def test_deterministic_entries_evict_under_pressure(self) -> None:
        """Same setup with a deterministic node should obey the cap."""
        cache = InMemoryNodeCache(max_entries=1)
        sched = TemporalScheduler(
            REGISTRY, cache=cache, time_source=FileClock()
        )
        await sched.run_batch(
            _single_node_graph(DetReporter.NODE_TYPE),
            total_duration_ms=3_000,
        )
        # Only the most recently-written window remains.
        assert len(cache) == 1


class TestParamOverride:
    """is_deterministic can be overridden per-instance via NodeData.parameters,
    matching the existing pattern for temporal_kind / window_spec / emit_policy /
    supports_reanneal.
    """

    async def test_override_flips_determinism(self, tmp_path: Path) -> None:
        """DetReporter (default is_deterministic=True) used with
        `parameters[is_deterministic]=False` produces a pinned cache
        entry."""
        cache = FilesystemNodeCache(tmp_path / "cache")
        sched = TemporalScheduler(
            REGISTRY, cache=cache, time_source=FileClock()
        )
        graph = GraphData(
            graph_id="g",
            nodes={
                "r": NodeData(
                    node_id="r",
                    node_type=DetReporter.NODE_TYPE,
                    parameters={"is_deterministic": False},
                ),
            },
        )
        await sched.run_batch(graph, total_duration_ms=2_000)
        files = list((tmp_path / "cache").glob("*.json"))
        assert len(files) >= 1
        # All entries should have is_deterministic=False because of the
        # per-instance override, even though the class default is True.
        for f in files:
            blob = json.loads(f.read_text())
            assert blob["is_deterministic"] is False
