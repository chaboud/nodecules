"""Every change of state is a named transform (vault ADR-0038).

The founder, 2026-10-10: "All migration takes one state of data and evolves
it to another by way of transform graph. Even primitive operations like 'add
audio buffer to strip'. Then we can make it fast with boxing."

A primitive write here is a registered, versioned transform: a handle
(`strip.append@1`), the kind of node it writes, and a pure function from
the node's previous data and the write's arguments to its next data. The
plain call (`write`) runs that function directly and commits the new
version together with a compact receipt: an envelope naming the transform,
the hash of the version it started from, and the hash of its arguments.
That is the unboxed path, and it is what every caller uses.

Boxing is materialising the same write as the full production graph: the
prior version and the arguments as source nodes, a declaration whose recipe
names the transform, and the generator producing it (`box`). One rule makes
the fast path safe: boxing changes the cost, never the identity. `verify`
rebuilds a write from its receipt through the boxed graph and checks the
hash matches; it is the ingot and casting relation of ADR-0014, with the
boxed graph as the reference and the plain call as the casting.

Arguments are kept out of the receipt when the transform can recover them
from the two versions (an append's elements are the new tail), so an audio
buffer is stored once, in the strip, and the receipt stays a few hashes.
"""

from __future__ import annotations

import asyncio
from dataclasses import dataclass
from typing import Any, Callable, Dict, List, Optional, Tuple

from .store import Edge, Manifest, Node, Store, canonical_hash, envelope_id, make_envelope

ApplyFn = Callable[[Optional[Any], Dict[str, Any]], Any]
RecoverFn = Callable[[Optional[Any], Any], Dict[str, Any]]


@dataclass(frozen=True)
class Transform:
    """A named way to take a node from one version to the next. `apply` must
    be a pure function of the previous data (None for a new node) and the
    arguments; `recover`, when given, returns the arguments from the previous
    and next data, so the receipt need not carry them."""

    handle: str
    kind: str
    apply: ApplyFn
    recover: Optional[RecoverFn] = None


REGISTRY: Dict[str, Transform] = {}


def register(t: Transform) -> Transform:
    existing = REGISTRY.get(t.handle)
    if existing is not None and existing is not t:
        raise ValueError(f"transform {t.handle!r} is already registered; a changed transform is a new version")
    REGISTRY[t.handle] = t
    return t


def transform(handle: str) -> Transform:
    try:
        return REGISTRY[handle]
    except KeyError:
        raise KeyError(f"no transform {handle!r}; registered: {sorted(REGISTRY)}") from None


class WriteRefused(ValueError):
    """A transform's precondition does not hold (an append to a closed
    strip): nothing is committed, and the reason is said."""


def write(
    store: Store,
    scope: str,
    name: str,
    handle: str,
    args: Optional[Dict[str, Any]] = None,
    *,
    author: str = "",
    note: str = "",
) -> Manifest:
    """The plain call: apply `handle` to `name`'s current version and commit
    the next version with its receipt, in one commit."""
    t = transform(handle)
    args = dict(args or {})
    tx = store.transaction(scope, author=author)
    prior = store.get(tx.base, name)
    prior_node = prior if isinstance(prior, Node) and prior.data is not None else None
    prior_data = prior_node.data if prior_node is not None else None
    data = t.apply(prior_data, args)
    node = Node(id=name, kind=t.kind, scope=scope, data=data, edges=prior_node.edges if prior_node is not None else ())
    node_hash = tx.put(node)
    tx.put(_receipt(t, node, prior_node, args, scope, tx.base.hash_of(name) if prior_node is not None else None, node_hash))
    return tx.commit(note=note or f"{handle} {name}")


def _receipt(t: Transform, node: Node, prior: Optional[Node], args: Dict[str, Any], scope: str, prior_hash: Optional[str] = None, node_hash: Optional[str] = None) -> Node:
    # The manifest already holds the prior version's hash; rehashing a long
    # strip to get it again doubled the cost of an append (measured).
    if prior_hash is None and prior is not None:
        prior_hash = prior.content_hash()
    args_hash = canonical_hash(args)
    recipe: Dict[str, Any] = {"transform": t.handle, "prior": prior_hash, "args": args_hash}
    if t.recover is None:
        recipe["args_value"] = args  # nothing to recover them from
    return make_envelope(
        node,
        content_hash=node_hash,
        recipe=recipe,
        inputs={f"{scope}:{node.id}": prior_hash} if prior_hash is not None else {},
        cache_key=canonical_hash({"transform": t.handle, "prior": prior_hash, "args": args_hash}),
        outcome="exact",
        reproducibility="exact",
        measured=False,
    )


def receipt(store: Store, manifest: Manifest, name: str) -> Optional[Dict[str, Any]]:
    """The transform receipt of `name` as bound by `manifest`, or None if the
    version there was not written by a transform."""
    env = store.get(manifest, envelope_id(name))
    if not isinstance(env, Node) or not isinstance(env.data, dict):
        return None
    recipe = env.data.get("recipe") or {}
    if "transform" not in recipe or env.data.get("content_hash") != manifest.hash_of(name):
        return None
    return dict(recipe)


def _prior_of(store: Store, manifest: Manifest, name: str, prior_hash: Optional[str]) -> Optional[Node]:
    if prior_hash is None:
        return None
    m = manifest
    while m is not None:
        if m.hash_of(name) == prior_hash:
            node = store.get(m, name)
            return node if isinstance(node, Node) else None
        m = store.manifest(m.parent) if m.parent else None
    return None


def args_of(store: Store, manifest: Manifest, name: str) -> Tuple[Optional[Node], Dict[str, Any]]:
    """The version a write started from and the arguments it was given,
    recovered from the receipt and the two versions."""
    r = receipt(store, manifest, name)
    if r is None:
        raise LookupError(f"{name!r} at seq {manifest.seq} has no transform receipt")
    t = transform(r["transform"])
    prior = _prior_of(store, manifest, name, r.get("prior"))
    if r.get("prior") is not None and prior is None:
        raise LookupError(f"the version {name!r} was written from is not reachable here")
    current = store.get(manifest, name)
    args = r["args_value"] if "args_value" in r else t.recover(prior.data if prior is not None else None, current.data)  # type: ignore[misc]
    if canonical_hash(args) != r["args"]:
        raise LookupError(f"the arguments recovered for {name!r} do not match the receipt")
    return prior, args


def box(store: Store, manifest: Manifest, name: str) -> Node:
    """Materialise the write that made `name`'s version in `manifest` as the
    full production graph, in a scratch store, and produce it: the prior
    version and the arguments as sources, a declaration whose recipe names
    the transform. Returns the node the graph produced, under `name`, with
    the edges the plain write kept."""
    from .generation import RECIPE_ROLE, RECIPE_TEMPLATE_KIND, Generator, Realization

    r = receipt(store, manifest, name)
    if r is None:
        raise LookupError(f"{name!r} at seq {manifest.seq} has no transform receipt")
    t = transform(r["transform"])
    prior, args = args_of(store, manifest, name)
    current = store.get(manifest, name)
    kind = current.kind if isinstance(current, Node) else t.kind

    async def cook(inputs: Dict[str, Any], params: Dict[str, Any]) -> Any:
        return t.apply(inputs.get("prior"), inputs["args"])

    scratch = Store()
    tx = scratch.transaction("lib", author="box")
    tx.put(Node(id="recipe", kind=RECIPE_TEMPLATE_KIND, scope="lib", data={"realization": t.handle, "params": {}}))
    tx.commit()
    tx = scratch.transaction("boxed", author="box")
    edges = [Edge(target="args", role="args"), Edge(target="recipe", scope="lib", role=RECIPE_ROLE)]
    tx.put(Node(id="args", kind="transform.args", scope="boxed", data=args))
    if prior is not None:
        tx.put(Node(id="prior", kind=prior.kind, scope="boxed", data=prior.data))
        edges.append(Edge(target="prior", role="prior"))
    tx.put(Node(id="next", kind=kind, scope="boxed", edges=tuple(edges)))
    tx.commit()
    g = asyncio.run(Generator(scratch, [Realization(t.handle, cook, deterministic=True)], author="box").produce("boxed", "next"))
    if g.node is None:
        raise RuntimeError(f"the boxed graph for {name!r} did not produce: {g.outcome} {g.error or g.note}")
    return Node(id=name, kind=kind, scope=manifest.scope, data=g.node.data, edges=current.edges if isinstance(current, Node) else ())


def verify(store: Store, manifest: Manifest, name: str) -> bool:
    """Boxing changes the cost, never the identity: the boxed graph rebuilt
    from the receipt produces the same hash the plain write committed."""
    return box(store, manifest, name).content_hash() == manifest.hash_of(name)


# --- the strip primitives -----------------------------------------------------------


def _strip_state(data: Any) -> Tuple[int, List[Any], bool]:
    if data is None:
        return 0, [], False
    if isinstance(data, list):
        return 0, list(data), False
    if isinstance(data, dict) and isinstance(data.get("elements"), list):
        return int(data.get("strip_floor", 0)), list(data["elements"]), bool(data.get("strip_closed", False))
    raise WriteRefused("not a strip")


def strip_data(floor: int, elements: List[Any], closed: bool = False) -> Any:
    """A strip's data: a plain list while it has released nothing and is
    open, so nothing that existed before changes; otherwise a dict carrying
    the floor and whether it is closed."""
    if floor == 0 and not closed:
        return list(elements)
    d: Dict[str, Any] = {"strip_floor": floor, "elements": list(elements)}
    if closed:
        d["strip_closed"] = True
    return d


def _append(prior: Optional[Any], args: Dict[str, Any]) -> Any:
    floor, elements, closed = _strip_state(prior)
    if closed:
        raise WriteRefused("the strip is closed; a source that comes back opens a new segment")
    return strip_data(floor, [*elements, *args["elements"]])


def _append_recover(prior: Optional[Any], new: Any) -> Dict[str, Any]:
    _, before, _ = _strip_state(prior)
    _, after, _ = _strip_state(new)
    return {"elements": after[len(before):]}


def _release(prior: Optional[Any], args: Dict[str, Any]) -> Any:
    floor, elements, closed = _strip_state(prior)
    n = int(args["released"])
    if n < 0 or n >= len(elements):
        raise WriteRefused(f"cannot release {n} of {len(elements)} retained elements; a strip keeps at least one")
    return strip_data(floor + n, elements[n:], closed)


def _release_recover(prior: Optional[Any], new: Any) -> Dict[str, Any]:
    return {"released": _strip_state(new)[0] - _strip_state(prior)[0]}


def _close(prior: Optional[Any], args: Dict[str, Any]) -> Any:
    floor, elements, closed = _strip_state(prior)
    if closed:
        raise WriteRefused("the strip is already closed")
    return strip_data(floor, elements, True)


def _close_recover(prior: Optional[Any], new: Any) -> Dict[str, Any]:
    return {}


STRIP_APPEND = register(Transform("strip.append@1", "strip", _append, _append_recover))
STRIP_RELEASE = register(Transform("strip.release@1", "strip", _release, _release_recover))
STRIP_CLOSE = register(Transform("strip.close@1", "strip", _close, _close_recover))


def _replace(prior: Optional[Any], args: Dict[str, Any]) -> Any:
    return args["data"]


# A whole-value write (a marker, a participant, an attention record): the
# arguments are the new value, so they go in the receipt as they are.
REPLACE = register(Transform("node.replace@1", "node", _replace))


def put_value(store: Store, scope: str, name: str, kind: str, data: Any, *, author: str = "", note: str = "") -> Manifest:
    """Write a whole value under `name` as a transform with a receipt. The
    node kind is the caller's; the transform is `node.replace@1`."""
    t = Transform(REPLACE.handle, kind, _replace)
    tx = store.transaction(scope, author=author)
    prior = store.get(tx.base, name)
    prior_node = prior if isinstance(prior, Node) and prior.data is not None else None
    node = Node(id=name, kind=kind, scope=scope, data=data, edges=prior_node.edges if prior_node is not None else ())
    node_hash = tx.put(node)
    tx.put(_receipt(t, node, prior_node, {"data": data}, scope, tx.base.hash_of(name) if prior_node is not None else None, node_hash))
    return tx.commit(note=note or f"{REPLACE.handle} {name}")


__all__ = [
    "REGISTRY",
    "REPLACE",
    "STRIP_APPEND",
    "STRIP_CLOSE",
    "STRIP_RELEASE",
    "Transform",
    "WriteRefused",
    "args_of",
    "box",
    "put_value",
    "receipt",
    "register",
    "strip_data",
    "transform",
    "verify",
    "write",
]
