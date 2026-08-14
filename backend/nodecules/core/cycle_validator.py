"""Strip-dependency cycle validator (PR-n7 scaffolding).

Rejects graphs with **within-window** strip cycles. Across windows,
temporal monotonicity prevents true cycles — a node reading its own
prior output via `strip.before()` or `strip.at(me - 1)` is fine. This
validator only flags cycles inside a single window.

Algorithm: build a node-to-node directed graph (`A -> B` iff B reads any
strip A writes), then DFS for cycles. The first cycle found is reported
with the participating node ids in topological order.

The validator is NOT wired into the scheduler in PR-n7 — calling it
requires nodes to declare `reads_strips` / `writes_strips`, which most
stenota nodes don't yet. PR-n7b's scheduler-v2 invokes it at graph-load
time after stenota's nodes have migrated. Until then, callers can run
the validator manually as a lint pass.

This is intentionally split from PR-n7's full scheduler rewrite: cycle
validation is small, well-isolated, easy to test, and useful on its
own. Shipping it now means the validator exists when stenota starts
declaring strip deps, even if scheduler-v2 takes longer to land.
"""

from __future__ import annotations

from collections import defaultdict
from typing import Any, Dict, Iterable, List, Optional, Set

from .types import GraphData


class StripCycleError(ValueError):
    """A within-window strip dependency cycle was detected."""

    def __init__(self, cycle: List[str]) -> None:
        self.cycle = cycle
        super().__init__(
            "strip-dependency cycle detected: "
            + " -> ".join(cycle)
            + "; within-window cycles are not permitted "
              "(cross-window feedback uses strip.before() / strip.at(me - 1) instead)"
        )


def validate_strip_cycles(
    graph: GraphData,
    node_registry: Dict[str, Any],
) -> None:
    """Raise `StripCycleError` if the graph contains a within-window cycle.

    A cycle is: node A writes strip X, node B reads strip X (and so depends
    on A), B writes strip Y, A reads strip Y (and so depends on B). Both
    edges are within the same logical window — there's no way to
    topologically order A and B for execution.

    Cross-window feedback is permitted: a node reading its own prior
    output via the substrate (`strip.at(me - 1)`) doesn't create a
    cycle here because the strip indexer answers from an earlier window
    that's already settled. The check only looks at declared strip
    deps, not at runtime access patterns; declaring a self-write +
    self-read on the same strip would be flagged (correctly — unless
    the node uses `strip.before()` it would loop).

    Unknown node types are silently skipped — the executor's clearer
    `Unknown node type` error fires later.
    """
    # writer_of_strip: strip_name -> [node_id, ...]. Multi-writer is
    # rare but legal (two nodes writing different filtered slices into
    # the same backing file). Cycle detection treats every writer as
    # a potential producer of dependencies onto the strip's readers.
    writer_of_strip: Dict[str, List[str]] = defaultdict(list)
    for node_id, node_data in graph.nodes.items():
        spec = _spec_for(node_data, node_registry)
        if spec is None:
            continue
        for strip in getattr(spec, "writes_strips", ()) or ():
            writer_of_strip[strip].append(node_id)

    # Edge set: producer -> consumer. A self-edge (a node writing and
    # reading the same strip) IS recorded — and will surface as a
    # 1-cycle below, which is the correct rejection (the only way to
    # read your own strip is via `strip.before()`, which doesn't
    # appear in the declared `reads_strips`).
    adj: Dict[str, Set[str]] = defaultdict(set)
    for node_id, node_data in graph.nodes.items():
        spec = _spec_for(node_data, node_registry)
        if spec is None:
            continue
        for strip in getattr(spec, "reads_strips", ()) or ():
            for writer in writer_of_strip.get(strip, []):
                adj[writer].add(node_id)

    # DFS for cycles. Colors: 0 = unvisited, 1 = on current stack,
    # 2 = fully processed.
    color: Dict[str, int] = defaultdict(int)
    stack: List[str] = []

    def dfs(start: str) -> Optional[List[str]]:
        color[start] = 1
        stack.append(start)
        for nxt in adj.get(start, ()):
            if color[nxt] == 1:
                # `nxt` is on the current stack — the cycle runs from
                # `nxt`'s position to the top, then back to `nxt`.
                idx = stack.index(nxt)
                return stack[idx:] + [nxt]
            if color[nxt] == 0:
                found = dfs(nxt)
                if found is not None:
                    return found
        color[start] = 2
        stack.pop()
        return None

    for node_id in graph.nodes:
        if color[node_id] == 0:
            cycle = dfs(node_id)
            if cycle is not None:
                raise StripCycleError(cycle)


def _spec_for(node_data: Any, registry: Dict[str, Any]) -> Optional[Any]:
    cls = registry.get(node_data.node_type)
    if cls is None:
        return None
    try:
        return cls().spec
    except Exception:
        return None


__all__ = [
    "StripCycleError",
    "validate_strip_cycles",
]
