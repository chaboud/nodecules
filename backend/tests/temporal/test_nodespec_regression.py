"""Regression guards for the static-DAG path under temporal additions.

These tests prove that every existing construction pattern of `NodeSpec` and
`ExecutionContext` still works with defaulted temporality fields, and that
existing builtin nodes (which don't know anything about temporality) keep
instantiating cleanly.
"""

from __future__ import annotations

from nodecules.core.temporal_context import ChunkedContext
from nodecules.core.time import FileClock, TimeRange
from nodecules.core.types import (
    EmitPolicy,
    ExecutionContext,
    GraphData,
    Mutability,
    NodeSpec,
    TemporalKind,
    WindowSpec,
)


class TestNodeSpecDefaults:
    def test_minimal_nodespec_construction(self) -> None:
        """Mirrors what every builtin node does today."""
        spec = NodeSpec(
            node_type="example",
            display_name="Example",
            description="An example node",
        )
        assert spec.temporal_kind == "static"
        assert spec.window_spec is None
        assert spec.emit_policy == "on_window_close"
        assert spec.supports_reanneal is False

    def test_positional_construction_still_works(self) -> None:
        """Some historical construction sites pass the four required args positionally."""
        spec = NodeSpec("pos_type", "Pos", "Positional construction.")
        assert spec.node_type == "pos_type"

    def test_windowed_construction_requires_window_spec_semantically(self) -> None:
        """Dataclass doesn't enforce the pairing, but callers can opt in."""
        spec = NodeSpec(
            node_type="windowed_example",
            display_name="Windowed Example",
            description="...",
            temporal_kind="windowed",
            window_spec=WindowSpec(size_ms=30_000, stride_ms=15_000),
        )
        assert spec.temporal_kind == "windowed"
        assert spec.window_spec is not None
        assert spec.window_spec.size_ms == 30_000


class TestWindowSpec:
    def test_basic_window(self) -> None:
        w = WindowSpec(size_ms=60_000, stride_ms=30_000)
        assert w.size_ms == 60_000
        assert w.stride_ms == 30_000
        assert w.align == "origin"
        assert w.min_upstream_coverage == 1.0

    def test_negative_size_rejected(self) -> None:
        import pytest

        with pytest.raises(ValueError):
            WindowSpec(size_ms=0, stride_ms=1_000)

    def test_coverage_out_of_range_rejected(self) -> None:
        import pytest

        with pytest.raises(ValueError):
            WindowSpec(size_ms=1_000, stride_ms=500, min_upstream_coverage=1.5)


class TestLiteralsAreUsable:
    def test_temporal_kind_values(self) -> None:
        # Typing check: `TemporalKind` accepts these four values.
        values: list[TemporalKind] = ["static", "windowed", "streaming", "reanneal"]
        assert len(values) == 4

    def test_emit_policy_values(self) -> None:
        values: list[EmitPolicy] = ["streaming", "on_window_close", "on_graph_close"]
        assert len(values) == 3

    def test_mutability_values(self) -> None:
        values: list[Mutability] = ["wet", "drying", "dry", "smudged"]
        assert len(values) == 4


class TestExecutionContextUnaffected:
    def test_bare_execution_context_still_constructs(self) -> None:
        ctx = ExecutionContext(
            execution_id="",
            graph=GraphData(graph_id="g"),
        )
        assert ctx.execution_id != ""  # auto-assigned in __post_init__
        assert ctx.graph.graph_id == "g"


class TestChunkedContextIsExecutionContext:
    def test_chunked_context_without_temporal_fields(self) -> None:
        ctx = ChunkedContext(
            execution_id="",
            graph=GraphData(graph_id="g"),
        )
        assert isinstance(ctx, ExecutionContext)
        assert ctx.current_window is None
        assert ctx.time_source is None

    def test_chunked_context_with_temporal_fields(self) -> None:
        window = TimeRange(start_ms=0, end_ms=60_000)
        ctx = ChunkedContext(
            execution_id="",
            graph=GraphData(graph_id="g"),
            current_window=window,
            time_source=FileClock(),
        )
        assert ctx.current_window == window
        assert ctx.time_source is not None


class TestBuiltinNodesPatternStillConstructs:
    """Regression guard using the exact construction pattern every builtin follows.

    We deliberately do NOT `from nodecules.plugins.builtin_nodes import ...`
    here because that module transitively imports the chat-context subsystem
    (`smart_context.py`, `content_addressable_context.py`) which pulls redis
    and postgres at import time. That's pre-existing tech debt against the
    `core/` layering invariant (see CLAUDE.md #4) and is scheduled for
    quarantine in a separate PR. The unit-test discipline says this test
    should check the dataclass defaults, not the chat stack.
    """

    def test_input_node_style_construction(self) -> None:
        """Mirrors `InputNode.__init__` exactly."""
        from nodecules.core.types import (
            DataType,
            ParameterSpec,
            PortSpec,
            ResourceRequirement,
        )

        spec = NodeSpec(
            node_type="input",
            display_name="Input",
            description="Provides input data to the graph",
            category="Input/Output",
            inputs=[],
            outputs=[
                PortSpec(name="output", data_type=DataType.ANY, description="Input data")
            ],
            parameters=[
                ParameterSpec(name="label", data_type="string", default=""),
                ParameterSpec(name="value", data_type="string", default=""),
                ParameterSpec(name="data_type", data_type="string", default="text"),
            ],
            resource_requirements=ResourceRequirement(),
        )
        assert spec.temporal_kind == "static"
        assert spec.emit_policy == "on_window_close"
        assert spec.supports_reanneal is False
        assert spec.window_spec is None

    def test_text_transform_style_construction(self) -> None:
        """Mirrors `TextTransformNode.__init__` exactly."""
        from nodecules.core.types import DataType, ParameterSpec, PortSpec

        spec = NodeSpec(
            node_type="text_transform",
            display_name="Text Transform",
            description="Transforms text using various operations",
            category="Text",
            inputs=[PortSpec(name="input", data_type=DataType.TEXT)],
            outputs=[PortSpec(name="output", data_type=DataType.TEXT)],
            parameters=[
                ParameterSpec(name="operation", data_type="string", default="uppercase"),
            ],
        )
        assert spec.temporal_kind == "static"
