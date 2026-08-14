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


class TestBuiltinNodesStillLoad:
    """Regression guard importing the REAL builtin nodes.

    `builtin_nodes.py` imports only `core.types` — it does NOT pull the
    chat-context subsystem (that is `immutable_chat_node.py` and
    `smart_chat_node.py`, which stay excluded here). An earlier revision of
    this guard asserted against a hand-copied `InputNode.__init__` on the
    mistaken belief that importing the module dragged in redis/postgres;
    a hand-copy cannot notice when a builtin's real spec changes, which is
    exactly the regression this guard exists to catch.
    """

    def test_all_builtins_instantiate_with_static_defaults(self) -> None:
        from nodecules.plugins.builtin_nodes import (
            InputNode,
            JsonCollectNode,
            JsonExtractNode,
            JsonReplaceNode,
            OutputNode,
            TextConcatNode,
            TextFilterNode,
            TextTransformNode,
        )

        for cls in (
            InputNode,
            TextTransformNode,
            TextFilterNode,
            TextConcatNode,
            JsonExtractNode,
            JsonReplaceNode,
            JsonCollectNode,
            OutputNode,
        ):
            spec = cls().spec
            assert spec.temporal_kind == "static", cls.__name__
            assert spec.emit_policy == "on_window_close", cls.__name__
            assert spec.supports_reanneal is False, cls.__name__
            assert spec.window_spec is None, cls.__name__

    def test_input_node_real_spec_shape(self) -> None:
        """Pin the observable interface of the real InputNode, not a copy."""
        from nodecules.plugins.builtin_nodes import InputNode

        spec = InputNode().spec
        assert spec.node_type == "input"
        assert [p.name for p in spec.outputs] == ["output"]
        assert [p.name for p in spec.parameters] == ["label", "value", "data_type"]

    def test_text_transform_real_spec_shape(self) -> None:
        from nodecules.plugins.builtin_nodes import TextTransformNode

        spec = TextTransformNode().spec
        assert spec.node_type == "text_transform"
        # The real input port is named "text" — the previous hand-copied guard
        # asserted "input" and stayed green, which is the drift this rewrite
        # exists to catch.
        assert [p.name for p in spec.inputs] == ["text"]
        assert [p.name for p in spec.outputs] == ["output"]
