"""Tests for PR-n7's strip-dependency cycle validator."""

from __future__ import annotations

import pytest

from nodecules.core.cycle_validator import (
    StripCycleError,
    validate_strip_cycles,
)
from nodecules.core.types import (
    BaseNode,
    DataType,
    GraphData,
    NodeData,
    NodeSpec,
    PortSpec,
)


# --- Synthetic nodes ----------------------------------------------------


def _make_node(
    node_type: str,
    *,
    reads: list[str] = None,
    writes: list[str] = None,
) -> type:
    """Build a BaseNode subclass declaring specific strip deps."""
    reads = reads or []
    writes = writes or []

    class _N(BaseNode):
        NODE_TYPE = node_type

        def __init__(self) -> None:
            super().__init__(
                NodeSpec(
                    node_type=node_type,
                    display_name=node_type,
                    description="",
                    outputs=[PortSpec(name="out", data_type=DataType.JSON)],
                    reads_strips=list(reads),
                    writes_strips=list(writes),
                )
            )

        async def execute(self, context, node_data):
            return {"out": True}

    _N.__name__ = f"Node_{node_type.replace('.', '_')}"
    return _N


def _registry(*classes: type) -> dict:
    return {c.NODE_TYPE: c for c in classes}


def _graph(*nodes: tuple[str, str]) -> GraphData:
    """Build a graph from `(node_id, node_type)` tuples."""
    return GraphData(
        graph_id="g",
        nodes={
            node_id: NodeData(node_id=node_id, node_type=node_type)
            for node_id, node_type in nodes
        },
    )


# --- Tests --------------------------------------------------------------


class TestNoCycle:
    def test_empty_graph(self) -> None:
        validate_strip_cycles(GraphData(graph_id="g"), {})

    def test_no_strip_decls(self) -> None:
        """A graph whose nodes don't declare strip deps cannot cycle
        through them."""
        N1 = _make_node("t.a")
        N2 = _make_node("t.b")
        validate_strip_cycles(
            _graph(("a", "t.a"), ("b", "t.b")),
            _registry(N1, N2),
        )

    def test_linear_chain(self) -> None:
        """A -> B -> C through distinct strips. No cycle."""
        N_a = _make_node("t.a", writes=["strips/x"])
        N_b = _make_node("t.b", reads=["strips/x"], writes=["strips/y"])
        N_c = _make_node("t.c", reads=["strips/y"])
        validate_strip_cycles(
            _graph(("a", "t.a"), ("b", "t.b"), ("c", "t.c")),
            _registry(N_a, N_b, N_c),
        )

    def test_diamond(self) -> None:
        """A -> {B, C} -> D through shared and distinct strips."""
        N_a = _make_node("t.a", writes=["strips/x"])
        N_b = _make_node("t.b", reads=["strips/x"], writes=["strips/y"])
        N_c = _make_node("t.c", reads=["strips/x"], writes=["strips/z"])
        N_d = _make_node("t.d", reads=["strips/y", "strips/z"])
        validate_strip_cycles(
            _graph(("a", "t.a"), ("b", "t.b"), ("c", "t.c"), ("d", "t.d")),
            _registry(N_a, N_b, N_c, N_d),
        )

    def test_unknown_node_type_skipped(self) -> None:
        """Unknown types don't crash the validator — executor's clearer
        error fires later."""
        validate_strip_cycles(
            _graph(("unknown", "t.does_not_exist")),
            {},
        )


class TestCycleDetected:
    def test_self_cycle(self) -> None:
        """A node writing AND reading the same strip is a 1-cycle.
        The only legal way to read your own strip is via
        `strip.before()`, which is a temporal feedback that doesn't
        appear in `reads_strips`."""
        N = _make_node("t.a", reads=["strips/x"], writes=["strips/x"])
        with pytest.raises(StripCycleError) as exc_info:
            validate_strip_cycles(_graph(("a", "t.a")), _registry(N))
        assert "a" in exc_info.value.cycle

    def test_two_node_cycle(self) -> None:
        """A writes X, B reads X + writes Y, A reads Y. Cycle."""
        N_a = _make_node("t.a", reads=["strips/y"], writes=["strips/x"])
        N_b = _make_node("t.b", reads=["strips/x"], writes=["strips/y"])
        with pytest.raises(StripCycleError) as exc_info:
            validate_strip_cycles(
                _graph(("a", "t.a"), ("b", "t.b")),
                _registry(N_a, N_b),
            )
        # Cycle should mention both nodes.
        nodes_in_cycle = set(exc_info.value.cycle)
        assert "a" in nodes_in_cycle
        assert "b" in nodes_in_cycle

    def test_three_node_cycle(self) -> None:
        """A -> B -> C -> A via shared strips."""
        N_a = _make_node("t.a", reads=["strips/z"], writes=["strips/x"])
        N_b = _make_node("t.b", reads=["strips/x"], writes=["strips/y"])
        N_c = _make_node("t.c", reads=["strips/y"], writes=["strips/z"])
        with pytest.raises(StripCycleError):
            validate_strip_cycles(
                _graph(("a", "t.a"), ("b", "t.b"), ("c", "t.c")),
                _registry(N_a, N_b, N_c),
            )

    def test_error_message_helpful(self) -> None:
        N_a = _make_node("t.a", reads=["strips/y"], writes=["strips/x"])
        N_b = _make_node("t.b", reads=["strips/x"], writes=["strips/y"])
        try:
            validate_strip_cycles(
                _graph(("a", "t.a"), ("b", "t.b")),
                _registry(N_a, N_b),
            )
        except StripCycleError as exc:
            msg = str(exc)
            assert "strip-dependency cycle" in msg
            # Mentions the cross-window-feedback alternative.
            assert "strip.before()" in msg
        else:
            pytest.fail("expected StripCycleError")


class TestPartialDeps:
    def test_one_node_with_deps_one_without(self) -> None:
        """Mixing a strip-aware node with a strip-naive node is fine."""
        N_a = _make_node("t.a", writes=["strips/x"])
        N_b = _make_node("t.b")  # no deps declared
        validate_strip_cycles(
            _graph(("a", "t.a"), ("b", "t.b")),
            _registry(N_a, N_b),
        )

    def test_writer_with_no_readers(self) -> None:
        """A producer with no consumers is acyclic."""
        N = _make_node("t.a", writes=["strips/x"])
        validate_strip_cycles(_graph(("a", "t.a")), _registry(N))

    def test_reader_with_no_writers(self) -> None:
        """A consumer of a strip that no one writes is also acyclic
        (and probably mis-wired — but the cycle validator's job is to
        check for cycles, not for dangling reads)."""
        N = _make_node("t.a", reads=["strips/x"])
        validate_strip_cycles(_graph(("a", "t.a")), _registry(N))
