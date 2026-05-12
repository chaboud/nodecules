"""Environment — declared capabilities + sinks for nodes (PR-n8).

Replaces the ad-hoc `execution_inputs["sidecar_path"]` pattern with a
typed capability registry. Nodes declare what they need via
`reads_env` / `writes_env` on `NodeSpec`; the scheduler validates at
graph instantiation time so missing capabilities fail with a clear
error, not "NoneType has no attribute".

Conventional capability names (use these to interoperate):

- `"sidecar"`          — `str | Path` sidecar directory root
- `"time"`             — `TimeSource` clock for the cooker
- `"llm.default"`      — default LLM provider adapter
- `"llm.<name>"`       — named provider override
- `"annotation_index"` — `AnnotationIndex`
- `"strips"`           — `StripRegistry`
- `"subscriptions"`    — `SubscriptionManager`

Conventional sink names:

- strip names (`"strips/claims/L3a@5min"`, ...) — append to backing JSONL
- signal names (`"signals/attention.requests"`, ...) — publish via sub mgr
- log names (`"logs/runtime"`, ...) — append to log file

**Scoping.** `with_overrides()` returns a new Environment that shadows
specified names while delegating other lookups to the parent. Used by
subgraph instantiation to override (e.g.) `llm.default` for one
sublens without disturbing the parent.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Dict, Optional, Set


class EnvironmentDepError(ValueError):
    """A node's declared env dependency isn't satisfied by the bound Environment."""


@dataclass
class Environment:
    """Read-mostly ambient capabilities + append-only sinks.

    Both maps are open-ended: names are strings, values are anything.
    Validation happens against `NodeSpec.reads_env` and `writes_env`,
    not against the Environment itself — the Environment is the
    *supply* side, NodeSpec is the *demand* side.

    `parent` enables delegate-on-miss lookup. `with_overrides()` builds
    a child Environment that shadows specific names; everything else
    falls through to the parent.
    """

    capabilities: Dict[str, Any] = field(default_factory=dict)
    sinks: Dict[str, Any] = field(default_factory=dict)
    parent: Optional["Environment"] = None

    # --- Lookup ----------------------------------------------------------

    def get_capability(self, name: str) -> Any:
        if name in self.capabilities:
            return self.capabilities[name]
        if self.parent is not None:
            return self.parent.get_capability(name)
        raise KeyError(
            f"no capability named {name!r}; available: "
            f"{sorted(self._all_capability_names())}"
        )

    def has_capability(self, name: str) -> bool:
        if name in self.capabilities:
            return True
        if self.parent is not None:
            return self.parent.has_capability(name)
        return False

    def get_sink(self, name: str) -> Any:
        if name in self.sinks:
            return self.sinks[name]
        if self.parent is not None:
            return self.parent.get_sink(name)
        raise KeyError(
            f"no sink named {name!r}; available: "
            f"{sorted(self._all_sink_names())}"
        )

    def has_sink(self, name: str) -> bool:
        if name in self.sinks:
            return True
        if self.parent is not None:
            return self.parent.has_sink(name)
        return False

    # --- Scoping ---------------------------------------------------------

    def with_overrides(
        self,
        capabilities: Optional[Dict[str, Any]] = None,
        sinks: Optional[Dict[str, Any]] = None,
    ) -> "Environment":
        """Return a new Environment that shadows specified names.

        A subgraph wanting `llm.default = ClaudeAdapter` for its interior
        while the parent uses `OllamaAdapter`:

            child = parent_env.with_overrides(
                capabilities={"llm.default": claude_adapter}
            )

        The parent is left untouched. Any name not shadowed delegates
        through `parent`.
        """
        return Environment(
            capabilities=dict(capabilities or {}),
            sinks=dict(sinks or {}),
            parent=self,
        )

    # --- Introspection helpers ------------------------------------------

    def _all_capability_names(self) -> Set[str]:
        out: Set[str] = set(self.capabilities)
        if self.parent is not None:
            out |= self.parent._all_capability_names()
        return out

    def _all_sink_names(self) -> Set[str]:
        out: Set[str] = set(self.sinks)
        if self.parent is not None:
            out |= self.parent._all_sink_names()
        return out


def validate_env_deps(
    graph: Any,
    environment: Environment,
    *,
    node_registry: Dict[str, Any],
) -> None:
    """Check every node in `graph`: declared `reads_env` / `writes_env`
    must be present on the Environment (or its parent chain).

    Raises `EnvironmentDepError` on the first unsatisfied dependency with
    a message naming the node and listing what IS available — so the
    failure is debuggable without a stack trace.

    Skipped entirely when a node's type isn't in the registry (the
    executor will raise a clearer "Unknown node type" later). Skipped
    when reads_env / writes_env are empty (no declared deps = no
    validation work).
    """
    for node_id, node_data in graph.nodes.items():
        cls = node_registry.get(node_data.node_type)
        if cls is None:
            continue
        spec = cls().spec
        for name in getattr(spec, "reads_env", ()) or ():
            if not environment.has_capability(name):
                raise EnvironmentDepError(
                    f"node {node_id!r} ({node_data.node_type!r}) declares "
                    f"reads_env={name!r} but no such capability is bound. "
                    f"Available: {sorted(environment._all_capability_names())}"
                )
        for name in getattr(spec, "writes_env", ()) or ():
            if not environment.has_sink(name):
                raise EnvironmentDepError(
                    f"node {node_id!r} ({node_data.node_type!r}) declares "
                    f"writes_env={name!r} but no such sink is bound. "
                    f"Available: {sorted(environment._all_sink_names())}"
                )


__all__ = [
    "Environment",
    "EnvironmentDepError",
    "validate_env_deps",
]
