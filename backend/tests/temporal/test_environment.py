"""Tests for PR-n8 Environment + reads_env / writes_env validation."""

from __future__ import annotations

import pytest

from nodecules.core.environment import (
    Environment,
    EnvironmentDepError,
    validate_env_deps,
)
from nodecules.core.types import (
    BaseNode,
    DataType,
    GraphData,
    NodeData,
    NodeSpec,
    PortSpec,
)


class TestEnvironmentBasics:
    def test_capability_get_has(self) -> None:
        env = Environment(capabilities={"sidecar": "/tmp/x", "time": object()})
        assert env.has_capability("sidecar")
        assert env.get_capability("sidecar") == "/tmp/x"

    def test_missing_capability_raises(self) -> None:
        env = Environment(capabilities={"sidecar": "/tmp/x"})
        assert not env.has_capability("time")
        with pytest.raises(KeyError, match="time"):
            env.get_capability("time")

    def test_sink_get_has(self) -> None:
        env = Environment(sinks={"strips/claims/L2": object()})
        assert env.has_sink("strips/claims/L2")
        assert env.get_sink("strips/claims/L2") is not None

    def test_error_messages_list_available(self) -> None:
        env = Environment(
            capabilities={"sidecar": "/tmp/x", "time": object()},
        )
        try:
            env.get_capability("missing")
        except KeyError as exc:
            assert "sidecar" in str(exc)
            assert "time" in str(exc)
        else:
            pytest.fail("expected KeyError")


class TestEnvironmentScoping:
    def test_parent_delegate(self) -> None:
        parent = Environment(capabilities={"sidecar": "/p"})
        child = parent.with_overrides()
        assert child.has_capability("sidecar")
        assert child.get_capability("sidecar") == "/p"

    def test_child_shadows_parent(self) -> None:
        parent = Environment(capabilities={"llm.default": "ollama"})
        child = parent.with_overrides(capabilities={"llm.default": "claude"})
        assert child.get_capability("llm.default") == "claude"
        # Parent unchanged.
        assert parent.get_capability("llm.default") == "ollama"

    def test_child_adds_new_caps(self) -> None:
        parent = Environment(capabilities={"sidecar": "/p"})
        child = parent.with_overrides(capabilities={"time": object()})
        # Child sees both.
        assert child.has_capability("sidecar")
        assert child.has_capability("time")
        # Parent only sees the original.
        assert parent.has_capability("sidecar")
        assert not parent.has_capability("time")

    def test_sinks_scope_independently(self) -> None:
        parent = Environment(sinks={"strips/a": 1})
        child = parent.with_overrides(sinks={"strips/a": 2})
        assert child.get_sink("strips/a") == 2
        assert parent.get_sink("strips/a") == 1

    def test_chain_of_overrides(self) -> None:
        a = Environment(capabilities={"x": "a"})
        b = a.with_overrides(capabilities={"y": "b"})
        c = b.with_overrides(capabilities={"z": "c"})
        assert c.has_capability("x")
        assert c.has_capability("y")
        assert c.has_capability("z")
        assert c.get_capability("x") == "a"


# --- Validation tests ----------------------------------------------------


class NodeWithDeps(BaseNode):
    NODE_TYPE = "test.with_deps"

    def __init__(self) -> None:
        super().__init__(
            NodeSpec(
                node_type=self.NODE_TYPE,
                display_name="With Deps",
                description="",
                inputs=[],
                outputs=[PortSpec(name="out", data_type=DataType.JSON)],
                reads_env=["sidecar", "time"],
                writes_env=["strips/claims/L2"],
            )
        )

    async def execute(self, context, node_data):
        return {"out": True}


class NodeNoDeps(BaseNode):
    NODE_TYPE = "test.no_deps"

    def __init__(self) -> None:
        super().__init__(
            NodeSpec(
                node_type=self.NODE_TYPE,
                display_name="No Deps",
                description="",
                inputs=[],
                outputs=[PortSpec(name="out", data_type=DataType.JSON)],
            )
        )

    async def execute(self, context, node_data):
        return {"out": True}


REGISTRY = {
    NodeWithDeps.NODE_TYPE: NodeWithDeps,
    NodeNoDeps.NODE_TYPE: NodeNoDeps,
}


def _make_graph(node_type: str) -> GraphData:
    return GraphData(
        graph_id="g",
        nodes={"n": NodeData(node_id="n", node_type=node_type)},
    )


class TestValidation:
    def test_satisfied_passes(self) -> None:
        env = Environment(
            capabilities={"sidecar": "/tmp", "time": object()},
            sinks={"strips/claims/L2": object()},
        )
        graph = _make_graph(NodeWithDeps.NODE_TYPE)
        # No exception.
        validate_env_deps(graph, env, node_registry=REGISTRY)

    def test_missing_capability_raises(self) -> None:
        env = Environment(
            capabilities={"sidecar": "/tmp"},  # missing "time"
            sinks={"strips/claims/L2": object()},
        )
        graph = _make_graph(NodeWithDeps.NODE_TYPE)
        with pytest.raises(EnvironmentDepError, match="time"):
            validate_env_deps(graph, env, node_registry=REGISTRY)

    def test_missing_sink_raises(self) -> None:
        env = Environment(
            capabilities={"sidecar": "/tmp", "time": object()},
            sinks={},  # missing the strip
        )
        graph = _make_graph(NodeWithDeps.NODE_TYPE)
        with pytest.raises(EnvironmentDepError, match="strips/claims/L2"):
            validate_env_deps(graph, env, node_registry=REGISTRY)

    def test_no_deps_passes_trivially(self) -> None:
        """A node that declares no reads_env/writes_env never fails
        validation, even against an empty env."""
        graph = _make_graph(NodeNoDeps.NODE_TYPE)
        validate_env_deps(graph, Environment(), node_registry=REGISTRY)

    def test_unknown_node_type_skipped(self) -> None:
        """Validation skips unknown node types so the executor's clearer
        'Unknown node type' error fires later."""
        graph = GraphData(
            graph_id="g",
            nodes={"n": NodeData(node_id="n", node_type="test.does_not_exist")},
        )
        validate_env_deps(graph, Environment(), node_registry=REGISTRY)

    def test_parent_chain_satisfies(self) -> None:
        """A capability provided by the parent satisfies the child."""
        parent = Environment(capabilities={"time": object()})
        child = parent.with_overrides(
            capabilities={"sidecar": "/tmp"},
            sinks={"strips/claims/L2": object()},
        )
        graph = _make_graph(NodeWithDeps.NODE_TYPE)
        validate_env_deps(graph, child, node_registry=REGISTRY)

    def test_message_lists_available(self) -> None:
        env = Environment(
            capabilities={"sidecar": "/tmp", "strips": object()},
        )
        graph = _make_graph(NodeWithDeps.NODE_TYPE)
        try:
            validate_env_deps(graph, env, node_registry=REGISTRY)
        except EnvironmentDepError as exc:
            msg = str(exc)
            # Tells you the unsatisfied one…
            assert "time" in msg
            # …and what IS bound, so you can diagnose.
            assert "sidecar" in msg
        else:
            pytest.fail("expected EnvironmentDepError")
