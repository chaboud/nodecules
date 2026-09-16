"""Deferral — a production that cannot run here becomes a request node
that something else advances.

Founder, 2026-09-17: partial results needing LLM consideration can be
turned into graph sub-events that get advanced, here by hand or by a
Claude Code instance on a machine with an inference engine. This is that
mechanism, and it is also dispatch across machines without a transport:
the request and the answer travel through the store (a shared directory,
or replica sync).

When the generator is asked to produce a node whose realization is not
in its inventory and it was built with `defer=True`, it commits a
`request` node (`requests/<node>`) recording the node, the realization
wanted, the inputs by identity, the params, and the cache key, and
returns the outcome `pending`. Anything that can run the realization
reads pending requests, cooks, and calls `fulfill`, which commits the
node's data with an envelope carrying that cache key and who fulfilled
it, and removes the request. The next `produce` is a cache hit and the
graph continues.
"""

from __future__ import annotations

from typing import Any, Dict, List, Optional

from .store import Manifest, Node, Store, make_envelope

REQUEST_KIND = "request"
REQUEST_PREFIX = "requests/"


def request_id(node_id: str) -> str:
    return f"{REQUEST_PREFIX}{node_id}"


def requests(store: Store, scope: str, manifest: Optional[Manifest] = None) -> List[Dict[str, Any]]:
    """Pending requests in a scope, oldest first by name."""
    m = manifest or store.current(scope)
    out: List[Dict[str, Any]] = []
    for name, h in sorted(m.entries()):
        if name.startswith(REQUEST_PREFIX):
            node = store.get_by_hash(h)
            if node is not None and node.data is not None:
                out.append({"request": name, **node.data})
    return out


def fulfill(store: Store, scope: str, node_id: str, data: Any, *, by: str, realization: Optional[str] = None, note: str = "") -> Manifest:
    """Commit the answer to a pending request: the node's data, an envelope
    that matches the request's cache key so the next production is a cache
    hit, and the request removed."""
    m = store.current(scope)
    req = store.get(m, request_id(node_id))
    if not isinstance(req, Node) or req.data is None:
        raise ValueError(f"no pending request for {node_id!r} in {scope!r}")
    declared = store.get(m, node_id)
    if not isinstance(declared, Node):
        raise ValueError(f"{node_id!r} is not declared in {scope!r}")
    produced = Node(id=node_id, kind=declared.kind, scope=scope, data=data, edges=declared.edges)
    env = make_envelope(
        produced,
        recipe={"realization": realization or req.data["realization"], "declared": req.data["realization"], "params": req.data.get("params", {}), "fulfilled_by": by},
        inputs=req.data.get("inputs", {}),
        cache_key=req.data["cache_key"],
        outcome="via-substitute" if (realization and realization != req.data["realization"]) else "exact",
        reproducibility="equivalent",
        measured=False,
    )
    tx = store.transaction(scope, author=by)
    tx.put(produced)
    tx.put(env)
    tx.delete(request_id(node_id))
    return tx.commit(note or f"fulfil {node_id} ({by})")


__all__ = ["REQUEST_KIND", "REQUEST_PREFIX", "fulfill", "request_id", "requests"]
