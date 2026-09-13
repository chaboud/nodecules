"""Replicas — manifests as a DAG, a deterministic merge, and sync.

The CRDT basis (founder, 2026-09-12: the network problem is why we need
one; it is what allows history-considerate mechanics). State-based, on
what the store already had:

- **Every replica commits locally**; nobody waits. A scope's history is
  a DAG of manifests: a merge manifest has two parents, the smaller hash
  as `parent` and the larger as `merge_parent`, so the same two heads
  merge to the *same* manifest hash on every replica.
- **The merge is deterministic and never raises.** With the nearest
  common ancestor as the base, per name: changed on one side only, take
  it; changed identically, take it; deleted on one side and changed on
  the other, keep the change; changed differently on both, fold through
  the kind's registered rule — called with the two sides in canonical
  (hash) order, so even a non-commutative rule converges — and, with no
  rule, bind a `conflict` node naming both versions. A conflict is a
  value, resolved by a later commit; on the replica plane there is no
  committer to stop and ask.
- **Sync is exchange of what the other side lacks, by hash**: the
  manifests reachable from its head and the bodies they bind. Content
  addressing means nothing is sent twice.
- **History is causal** because a manifest's hash covers its parents. The
  DAG is the vector clock: "what had this replica seen" is the set of
  ancestors of its head.

Two replicas that sync in either order end with identical heads — hash
for hash — which is the convergence property the tests hold this to.
Not here: an operations log for kinds that need intent (that is a strip
of operations with a fold, PR-r15-adjacent), tombstone garbage collection,
signed manifests, and any transport.
"""

from __future__ import annotations

import heapq
from typing import Dict, Iterator, List, Optional, Set, Tuple

from .pmap import PMap, diff as pmap_diff
from .store import Manifest, Node, Store, get_merge

CONFLICT_KIND = "conflict"


def ancestors(store: Store, head: Manifest) -> Iterator[Manifest]:
    """`head` and everything it descends from, breadth-first."""
    seen: Set[str] = set()
    queue: List[Manifest] = [head]
    while queue:
        m = queue.pop(0)
        h = m.content_hash()
        if h in seen:
            continue
        seen.add(h)
        yield m
        for p in m.parents:
            pm = store.manifest(p)
            if pm is not None:
                queue.append(pm)


def is_ancestor(store: Store, maybe: Manifest, of: Manifest) -> bool:
    target = maybe.content_hash()
    return any(m.content_hash() == target for m in ancestors(store, of))


def common_ancestor(store: Store, a: Manifest, b: Manifest) -> Optional[Manifest]:
    """The nearest manifest both descend from: among b's ancestors that are
    also a's, the one with the highest sequence number. None when the
    histories share nothing."""
    from_a = {m.content_hash() for m in ancestors(store, a)}
    heap: List[Tuple[int, str]] = []
    seen: Set[str] = set()
    heapq.heappush(heap, (-b.seq, b.content_hash()))
    while heap:
        _neg, h = heapq.heappop(heap)
        if h in seen:
            continue
        seen.add(h)
        m = store.manifest(h)
        if m is None:
            continue
        if h in from_a:
            return m
        for p in m.parents:
            pm = store.manifest(p)
            if pm is not None:
                heapq.heappush(heap, (-pm.seq, p))
    return None


def _conflict(scope: str, name: str, base: Optional[str], versions: Tuple[str, str]) -> Node:
    return Node(
        id=name,
        kind=CONFLICT_KIND,
        scope=scope,
        data={"base": base, "versions": sorted(versions)},
    )


def merge(store: Store, a: Manifest, b: Manifest, *, author: str = "merge") -> Manifest:
    """The deterministic three-way merge of two heads of one scope. Returns
    `a` or `b` when one already contains the other. Registers the merge
    manifest with the store but does not move the head."""
    if a.scope != b.scope:
        raise ValueError("cannot merge manifests of different scopes")
    ha, hb = a.content_hash(), b.content_hash()
    if ha == hb or is_ancestor(store, b, a):
        return a
    if is_ancestor(store, a, b):
        return b
    base = common_ancestor(store, a, b)
    base_entries = base._entries if base is not None else PMap()
    entries = base_entries
    names = set(pmap_diff(base_entries, a._entries).keys) | set(pmap_diff(base_entries, b._entries).keys)
    for name in sorted(names):
        vb = base_entries.get(name)
        va, vc = a.hash_of(name), b.hash_of(name)
        if va == vc:
            result = va
        elif va == vb:
            result = vc
        elif vc == vb:
            result = va
        elif va is None:
            result = vc  # a deleted, b changed: keep the change
        elif vc is None:
            result = va
        else:
            lo, hi = sorted((va, vc))
            n_lo, n_hi = store.get_by_hash(lo), store.get_by_hash(hi)
            rule = get_merge(n_lo.kind) if (n_lo is not None and n_hi is not None and n_lo.kind == n_hi.kind) else None
            if rule is not None:
                base_node = store.get_by_hash(vb) if vb else None
                merged = rule(base_node, n_lo, n_hi)
                if merged.id != name or merged.scope != a.scope:
                    raise ValueError(f"merge rule for {n_lo.kind!r} returned {merged.id!r} in {merged.scope!r}")
                result = store.import_body(merged)
            else:
                result = store.import_body(_conflict(a.scope, name, vb, (va, vc)))
        entries = entries.set(name, result) if result is not None else entries.delete(name)
    lo, hi = sorted((ha, hb))
    newer = a if (a.resolution_version, ha) >= (b.resolution_version, hb) else b
    m = Manifest.build(
        a.scope,
        entries,
        seq=max(a.seq, b.seq) + 1,
        parent=lo,
        merge_parent=hi,
        note="merge",
        author=author,
        resolution=newer.resolution,
        resolution_version=newer.resolution_version,
    )
    store.import_manifest(m)
    return m


def merge_head(store: Store, scope: str, other: Manifest, *, author: str = "merge") -> Manifest:
    """Bring another head into this replica's scope: fast-forward if it
    contains ours, keep ours if we contain it, otherwise merge. Moves the
    head and returns it."""
    store.import_manifest(other)
    cur = store.current(scope)
    result = merge(store, cur, other, author=author)
    if result is not cur:
        store.set_head(scope, result)
    return store.current(scope)


def transfer(src: Store, dst: Store, scope: str) -> Tuple[int, int]:
    """Copy what `dst` lacks of `src`'s history for `scope`: manifests
    reachable from src's head and the bodies they bind. Returns (manifests,
    bodies) moved. Does not move dst's head."""
    moved_m = moved_b = 0
    for m in ancestors(src, src.current(scope)):
        h = m.content_hash()
        if dst.manifest(h) is not None:
            continue
        for _name, body_hash in m.entries():
            if not dst.has_body(body_hash) and src.has_body(body_hash):
                node = src.get_by_hash(body_hash)
                assert node is not None
                dst.import_body(node)
                moved_b += 1
        dst.import_manifest(m)
        moved_m += 1
    return moved_m, moved_b


def sync(a: Store, b: Store, scope: str, *, author: str = "merge") -> Tuple[Manifest, Manifest]:
    """Exchange both ways and merge both ways. Afterwards both replicas
    have the same head for `scope`, hash for hash."""
    transfer(b, a, scope)
    merge_head(a, scope, b.current(scope), author=author)
    transfer(a, b, scope)
    merge_head(b, scope, a.current(scope), author=author)
    return a.current(scope), b.current(scope)


__all__ = [
    "CONFLICT_KIND",
    "ancestors",
    "common_ancestor",
    "is_ancestor",
    "merge",
    "merge_head",
    "sync",
    "transfer",
]
