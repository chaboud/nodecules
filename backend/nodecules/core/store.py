"""The node store — PR-r3a, the declarative core.

REFERENCE-MODEL Part I says the whole model is: nodes (id, kind, scope,
data, edges), kinds, scopes with per-scope manifests, typed edges,
generation, manifests as version anchors. This module is the store those
sit in — §§7, 9, 15, 16 of Part II — built on `core/pmap.py` and nothing
else. No database, no filesystem yet (sparse disk load is PR-r3b).

The decisions this slice makes (vault ADR-0023):

- **An id is a name; the content hash is the version.** `Node.content_hash`
  covers kind + data + edges and *excludes* id and scope. Bodies are stored
  once by hash; a manifest is the map from names to hashes for one scope at
  one version. Two names bound to the same content share one body.
- **A manifest is a node, and its root is the trie's Merkle hash.** Two
  scopes — or two replicas — holding the same name→hash set have the same
  `root` without coordination (P-26); the manifest's own content hash adds
  `seq` and `parent`, so it is a version id in the git sense: tree hash vs
  commit hash.
- **Reads are wait-free.** `snapshot()` hands back the current manifest
  object; every read goes through a manifest and never sees a later commit.
- **Writes are per-scope CAS.** A `Transaction` stages puts and deletes
  against a base manifest and commits by compare-and-swap on the scope's
  current pointer. If the pointer moved, the commit rebases when the
  intervening changes touch disjoint names (§22: "writes touch disjoint
  leaves; CAS retry resolves trivially") and raises `Conflict` naming the
  overlap otherwise.
- **Residency is a store-side axis that never touches identity**
  (ADR-0018 condition 2). `prune` drops a body's *data* — the expensive
  part — and keeps its skeleton (kind + edges), so the manifest, the root
  hash, lineage, and every composed hash are unchanged. A read then returns
  `Absent(rebuild_via=<envelope id>)` when an envelope exists and `Lost`
  when none does; both still say what kind the node was and what it
  depended on. `restore` re-admits bytes that hash to what the manifest
  says — fetching from a replica and recomputing from ingredients are the
  same operation from the store's point of view.
- **The composed hash is the cache key** (ADR-0003) and is computable from
  a manifest alone — bodies may be pruned; identity survives compaction,
  which is the P-24 answer ("what was the hash here" is always answerable).
- **Decoration never bonds into functional nodes** (ADR-0022): a functional
  node whose edge targets a decoration namespace is refused at `put`.

Not in this slice, on purpose: refcounts and retention curves, kinds as a
registry with schemas, the generation engine, indexes, markers, disk. The
IIR cell shape `(output, carried_state)` (§17) is a data convention the
store is agnostic to until PR-r15 grounds it.
"""

from __future__ import annotations

import hashlib
import json
import threading
from typing import Any, Dict, Iterator, List, Literal, Mapping, Optional, Set, Tuple, Union

from pydantic import BaseModel, ConfigDict, Field, PrivateAttr

from .descriptions import DECORATION_NAMESPACES
from .pmap import Diff, PMap, diff as pmap_diff
from .strip_access import AccessPattern, AllPattern

MANIFEST_KIND = "manifest"
ENVELOPE_KIND = "envelope"

Residency = Literal["ram", "pruned"]


def canonical_hash(payload: Any) -> str:
    """sha256 over canonical JSON — the same convention `descriptions.py`
    and `node_cache.py` use, so hashes compose across modules."""
    canonical = json.dumps(payload, sort_keys=True, separators=(",", ":"))
    return hashlib.sha256(canonical.encode("utf-8")).hexdigest()


# --- Nodes ------------------------------------------------------------------


class Edge(BaseModel):
    """A typed reference to another node (REFERENCE-MODEL §5). `target` is a
    name in `scope` (None = the source node's own scope); `pattern` is the
    PR-r1 access-pattern ADT; `role` is the consumer-side port name."""

    model_config = ConfigDict(frozen=True)

    target: str
    scope: Optional[str] = None
    pattern: AccessPattern = Field(default_factory=AllPattern)
    role: str = ""


class Node(BaseModel):
    """One node. Immutable; a "change" is a new node under the same name,
    bound by a new manifest. `data` is any JSON-able payload, or None for a
    node that is described but not yet produced (§2)."""

    model_config = ConfigDict(frozen=True)

    id: str
    kind: str
    scope: str
    data: Optional[Any] = None
    edges: Tuple[Edge, ...] = ()

    def body(self) -> Dict[str, Any]:
        """What identity covers: kind, data, edges. Not the name, not the
        scope — those are addresses (§13: the store is {(scope, id) → node})."""
        return {
            "kind": self.kind,
            "data": self.data,
            "edges": [e.model_dump(mode="json") for e in self.edges],
        }

    def content_hash(self) -> str:
        # Not cached: pydantic copies private state on `model_copy`, and a
        # stale cached hash on a copy with different content is exactly the
        # silent failure this store exists to prevent.
        return canonical_hash(self.body())

    def is_decoration(self) -> bool:
        return any(self.id.startswith(ns) for ns in DECORATION_NAMESPACES)


class Absent(BaseModel):
    """The name is bound and its identity is known, but the data is not
    resident — and an envelope says how to rebuild it (§16). The skeleton
    (kind, edges) is still here: lineage survives pruning."""

    model_config = ConfigDict(frozen=True)

    id: str
    scope: str
    kind: str
    content_hash: str
    edges: Tuple[Edge, ...] = ()
    rebuild_via: str


class Lost(BaseModel):
    """The name is bound and its identity is known, but the data is not
    resident and nothing describes how to rebuild it (§6 outcome 4)."""

    model_config = ConfigDict(frozen=True)

    id: str
    scope: str
    kind: str
    content_hash: str
    edges: Tuple[Edge, ...] = ()


Resolved = Union[Node, Absent, Lost]


# --- Envelopes ----------------------------------------------------------------


def envelope_id(content_id: str) -> str:
    """The naming convention for a node's rebuild slate (§16, §28 node-id
    format: opaque ids, prefixed naming as a human-readable convention)."""
    return f"envelope:{content_id}"


def make_envelope(
    node: Node,
    *,
    recipe: Mapping[str, Any],
    inputs: Mapping[str, str],
    cooked_at_ms: Optional[int] = None,
    state: str = "dry",
) -> Node:
    """Build the envelope for `node`: a distinct node that names the content,
    pins its identity, records the recipe and the input versions it was
    cooked from, and carries the same edges so lineage survives pruning."""
    return Node(
        id=envelope_id(node.id),
        kind=ENVELOPE_KIND,
        scope=node.scope,
        data={
            "of": node.id,
            "content_hash": node.content_hash(),
            "recipe": dict(recipe),
            "inputs": dict(inputs),
            "state": state,
            "cooked_at_ms": cooked_at_ms,
        },
        edges=node.edges,
    )


# --- Manifests ----------------------------------------------------------------


class Manifest(BaseModel):
    """One scope at one version: names → content hashes. Holding one is a
    wait-free snapshot (§7). It is itself a node (`as_node`); `root` is the
    content-only identity, `content_hash()` the version identity."""

    model_config = ConfigDict(frozen=True, arbitrary_types_allowed=True)

    scope: str
    seq: int
    parent: Optional[str]
    root: str
    note: str = ""

    _entries: PMap = PrivateAttr(default_factory=PMap)

    @classmethod
    def build(
        cls,
        scope: str,
        entries: PMap,
        *,
        seq: int,
        parent: Optional[str],
        note: str = "",
    ) -> "Manifest":
        m = cls(scope=scope, seq=seq, parent=parent, root=entries.root_hash, note=note)
        m._entries = entries
        return m

    def hash_of(self, node_id: str) -> Optional[str]:
        return self._entries.get(node_id)

    def __contains__(self, node_id: object) -> bool:
        return node_id in self._entries

    def __len__(self) -> int:
        return len(self._entries)

    def ids(self) -> Iterator[str]:
        return iter(self._entries)

    def entries(self) -> Iterator[Tuple[str, str]]:
        return self._entries.items()

    def as_node(self) -> Node:
        return Node(
            id=f"manifest:{self.scope}#{self.seq}",
            kind=MANIFEST_KIND,
            scope=self.scope,
            data={
                "scope": self.scope,
                "seq": self.seq,
                "parent": self.parent,
                "root": self.root,
                "note": self.note,
            },
        )

    def content_hash(self) -> str:
        return self.as_node().content_hash()


class Snapshot:
    """A consistent set of manifests, one per scope, chosen by the reader.
    Cross-scope reads resolve through the manifest held here when the scope
    is present and through the scope's live current otherwise — the
    `snapshot | latest` choice of §28, made by what the reader holds."""

    def __init__(self, store: "Store", manifests: Mapping[str, Manifest]) -> None:
        self._store = store
        self._manifests = dict(manifests)

    def manifest(self, scope: str) -> Manifest:
        m = self._manifests.get(scope)
        return m if m is not None else self._store.current(scope)

    def scopes(self) -> Tuple[str, ...]:
        return tuple(sorted(self._manifests))

    def pinned(self, scope: str) -> bool:
        return scope in self._manifests


class Conflict(Exception):
    """A CAS commit found the scope moved under it on names it also touched."""

    def __init__(self, scope: str, keys: Set[str]) -> None:
        self.scope = scope
        self.keys = frozenset(keys)
        super().__init__(f"conflict in scope {scope!r} on {sorted(keys)}")


class DanglingEdge(Exception):
    """A composed hash walked into a name the manifest does not bind."""


class Cycle(Exception):
    """A composed hash walked back into a node still being hashed."""


# --- The store ----------------------------------------------------------------


class Store:
    """Content-addressed bodies plus per-scope manifest pointers. Everything
    a reader needs is reachable from a manifest; everything a writer does
    goes through a `Transaction`."""

    def __init__(self) -> None:
        self._blobs: Dict[str, Node] = {}
        self._skeletons: Dict[str, Tuple[str, Tuple[Edge, ...]]] = {}
        self._residency: Dict[str, Residency] = {}
        self._current: Dict[str, Manifest] = {}
        self._manifests: Dict[str, Manifest] = {}
        self._lock = threading.Lock()

    # -- scopes and snapshots ----------------------------------------------------

    def scopes(self) -> Tuple[str, ...]:
        return tuple(sorted(self._current))

    def current(self, scope: str) -> Manifest:
        """The live manifest of a scope; a scope springs into being with an
        empty genesis manifest (seq 0, no parent) on first touch."""
        m = self._current.get(scope)
        if m is None:
            with self._lock:
                m = self._current.get(scope)
                if m is None:
                    m = Manifest.build(scope, PMap(), seq=0, parent=None, note="genesis")
                    self._manifests[m.content_hash()] = m
                    self._current[scope] = m
        return m

    def snapshot(self, *scopes: str) -> Snapshot:
        """Pin the current manifest of each named scope. Wait-free: this is
        a pointer read per scope."""
        return Snapshot(self, {s: self.current(s) for s in scopes})

    def manifest(self, content_hash: str) -> Optional[Manifest]:
        return self._manifests.get(content_hash)

    def history(self, scope: str) -> Iterator[Manifest]:
        """Walk the parent chain from the current manifest to genesis."""
        m: Optional[Manifest] = self.current(scope)
        while m is not None:
            yield m
            m = self._manifests.get(m.parent) if m.parent else None

    def transaction(self, scope: str) -> "Transaction":
        return Transaction(self, scope, self.current(scope))

    # -- reads -------------------------------------------------------------------

    def get(self, manifest: Manifest, node_id: str) -> Optional[Resolved]:
        """Resolve a name through a manifest. None when the name is unbound;
        `Node` when resident; `Absent` when pruned with an envelope; `Lost`
        when pruned without one. Never blocks."""
        h = manifest.hash_of(node_id)
        if h is None:
            return None
        node = self._blobs.get(h)
        if node is not None and self._residency.get(h) == "ram":
            return node
        kind, edges = self._skeletons[h]
        env = manifest.hash_of(envelope_id(node_id))
        if env is not None and self._residency.get(env) == "ram":
            return Absent(
                id=node_id,
                scope=manifest.scope,
                kind=kind,
                content_hash=h,
                edges=edges,
                rebuild_via=envelope_id(node_id),
            )
        return Lost(id=node_id, scope=manifest.scope, kind=kind, content_hash=h, edges=edges)

    def get_by_hash(self, content_hash: str) -> Optional[Node]:
        """A body by identity, whatever it is named. None if not resident."""
        if self._residency.get(content_hash) != "ram":
            return None
        return self._blobs.get(content_hash)

    def resolve(self, snapshot: Snapshot, scope: str, node_id: str) -> Optional[Resolved]:
        return self.get(snapshot.manifest(scope), node_id)

    def composed_hash(self, snapshot: Snapshot, scope: str, node_id: str) -> str:
        """The two-layer identity of ADR-0003: a node's own content hash
        composed with the composed hashes of everything its edges reach, as
        bound by the manifests the snapshot holds. This is the cache key.
        Needs manifests only — pruned bodies still have identity."""
        memo: Dict[Tuple[str, str], str] = {}
        return self._composed(snapshot, scope, node_id, memo, set())

    def _composed(
        self,
        snapshot: Snapshot,
        scope: str,
        node_id: str,
        memo: Dict[Tuple[str, str], str],
        visiting: Set[Tuple[str, str]],
    ) -> str:
        key = (scope, node_id)
        if key in memo:
            return memo[key]
        if key in visiting:
            raise Cycle(f"{scope}:{node_id} reaches itself")
        manifest = snapshot.manifest(scope)
        own = manifest.hash_of(node_id)
        if own is None:
            raise DanglingEdge(f"{scope}:{node_id} is not bound in manifest seq {manifest.seq}")
        _kind, edges = self._skeletons[own]  # present for every admitted identity
        visiting.add(key)
        parts: List[Tuple[str, str, str]] = []
        for e in edges:
            target_scope = e.scope or scope
            parts.append(
                (e.role, f"{target_scope}:{e.target}", self._composed(snapshot, target_scope, e.target, memo, visiting))
            )
        visiting.discard(key)
        composed = canonical_hash({"self": own, "edges": parts})
        memo[key] = composed
        return composed

    def diff(self, old: Manifest, new: Manifest) -> Diff:
        """Names added, removed, or re-bound between two manifests of one
        scope, at a cost proportional to the change (shared subtrees skip)."""
        if old.scope != new.scope:
            raise ValueError(f"manifests are of different scopes: {old.scope!r} vs {new.scope!r}")
        return pmap_diff(old._entries, new._entries)

    # -- residency ---------------------------------------------------------------

    def residency(self, content_hash: str) -> Optional[Residency]:
        return self._residency.get(content_hash)

    def prune(self, content_hash: str) -> None:
        """Drop a body's data; keep its skeleton. Identity, manifests,
        lineage, and composed hashes are untouched — this is a residency
        change, not a content change."""
        if content_hash not in self._blobs:
            raise KeyError(content_hash)
        self._blobs.pop(content_hash)
        self._residency[content_hash] = "pruned"

    def restore(self, node: Node) -> str:
        """Re-admit bytes for a pruned identity. Whether they came from a
        replica or a recompute is not the store's concern; that they hash
        to what the manifest says is."""
        h = node.content_hash()
        if self._residency.get(h) != "pruned":
            raise ValueError(f"{h[:12]} is not a pruned identity in this store")
        self._blobs[h] = node
        self._residency[h] = "ram"
        return h

    # -- internal ----------------------------------------------------------------

    def _admit(self, node: Node) -> str:
        h = node.content_hash()
        if h not in self._blobs:
            self._blobs[h] = node
            self._skeletons[h] = (node.kind, node.edges)
            self._residency[h] = "ram"
        return h

    def _commit(self, tx: "Transaction", note: str) -> Manifest:
        with self._lock:
            live = self._current[tx.scope]
            base = tx.base
            if live is not base:
                moved = pmap_diff(base._entries, live._entries).keys
                overlap = moved & tx.touched
                if overlap:
                    raise Conflict(tx.scope, set(overlap))
                base = live
            entries = base._entries
            for node_id, node in tx.puts.items():
                entries = entries.set(node_id, self._admit(node))
            for node_id in tx.deletes:
                entries = entries.delete(node_id)
            manifest = Manifest.build(
                tx.scope, entries, seq=base.seq + 1, parent=base.content_hash(), note=note
            )
            self._admit(manifest.as_node())
            self._manifests[manifest.content_hash()] = manifest
            self._current[tx.scope] = manifest
            return manifest


class Transaction:
    """N puts and deletes against one scope, committed atomically by CAS on
    the scope's current-manifest pointer (§15)."""

    def __init__(self, store: Store, scope: str, base: Manifest) -> None:
        self._store = store
        self.scope = scope
        self.base = base
        self.puts: Dict[str, Node] = {}
        self.deletes: Set[str] = set()
        self.committed: Optional[Manifest] = None

    @property
    def touched(self) -> frozenset:
        return frozenset(self.puts) | frozenset(self.deletes)

    def put(self, node: Node) -> str:
        """Stage a node under its own id. Returns its content hash."""
        if self.committed is not None:
            raise RuntimeError("transaction already committed")
        if node.scope != self.scope:
            raise ValueError(
                f"node {node.id!r} is in scope {node.scope!r}; this transaction is on {self.scope!r}"
            )
        if not node.is_decoration():
            for e in node.edges:
                for ns in DECORATION_NAMESPACES:
                    if e.target.startswith(ns):
                        raise ValueError(
                            f"{node.id!r} bonds to decoration {e.target!r}: functional nodes "
                            "may not cite claims, hallmarks, vouches, executors, plans, or observations"
                        )
        self.deletes.discard(node.id)
        self.puts[node.id] = node
        return node.content_hash()

    def delete(self, node_id: str) -> None:
        """Unbind a name in the new manifest. The body stays for older
        manifests; reclaiming it is retention's job (PR-r3b)."""
        if self.committed is not None:
            raise RuntimeError("transaction already committed")
        self.puts.pop(node_id, None)
        self.deletes.add(node_id)

    def commit(self, note: str = "") -> Manifest:
        if self.committed is not None:
            raise RuntimeError("transaction already committed")
        self.committed = self._store._commit(self, note)
        return self.committed


__all__ = [
    "Absent",
    "Conflict",
    "Cycle",
    "DanglingEdge",
    "ENVELOPE_KIND",
    "Edge",
    "Lost",
    "MANIFEST_KIND",
    "Manifest",
    "Node",
    "Residency",
    "Resolved",
    "Snapshot",
    "Store",
    "Transaction",
    "canonical_hash",
    "envelope_id",
    "make_envelope",
]
