"""Persistent hash-array-mapped trie with a *deterministic* shape.

This is the data structure the node store (`core/store.py`) stands on —
REFERENCE-MODEL §9: a sparse, copy-on-write, structurally-shared map whose
old versions stay valid snapshots after every write.

Two properties matter more than speed here, and both are tested:

1. **Shape is a pure function of the key set.** The same keys produce the
   same trie regardless of insertion order, and regardless of whether other
   keys were inserted and deleted along the way. Keys are placed by the
   sha256 of the key (not Python's salted `hash`), and a delete lifts a lone
   leaf back up so the trie is always in the form fresh insertion would have
   produced. This is what the vault's P-26 asks of the data graph: two
   replicas holding the same content agree on the same root hash without
   talking to each other, so diffing is coordination-free.

2. **Every node carries a Merkle hash.** `root_hash` is O(1) after
   construction and changes iff the mapping changes; `diff` skips any
   subtree the two sides share (by identity or by hash), so comparing two
   versions costs the size of the change, not the size of the map.

Values must be JSON-serialisable — in the store they are content-hash
strings. Iteration order is trie order (by key hash), which is stable but
not alphabetical.
"""

from __future__ import annotations

import hashlib
import json
from typing import Any, Dict, Iterator, Optional, Tuple

BITS = 5
WIDTH = 1 << BITS  # 32-way
MASK = WIDTH - 1
HASH_BITS = 256
MAX_DEPTH = HASH_BITS // BITS  # 51 levels before the key hash is exhausted


def key_hash(key: str) -> int:
    """Deterministic placement hash for a key — the same on every machine."""
    return int.from_bytes(hashlib.sha256(key.encode("utf-8")).digest(), "big")


def _value_hash(value: Any) -> str:
    canonical = json.dumps(value, sort_keys=True, separators=(",", ":"))
    return hashlib.sha256(canonical.encode("utf-8")).hexdigest()


def _chunk(khash: int, depth: int) -> int:
    return (khash >> (depth * BITS)) & MASK


class _Leaf:
    __slots__ = ("key", "value", "khash", "hash")

    def __init__(self, key: str, value: Any, khash: int) -> None:
        self.key = key
        self.value = value
        self.khash = khash
        self.hash = hashlib.sha256(
            f"L:{khash:064x}:{_value_hash(value)}".encode("ascii")
        ).hexdigest()


class _Branch:
    __slots__ = ("bitmap", "children", "hash")

    def __init__(self, bitmap: int, children: Tuple[Any, ...]) -> None:
        self.bitmap = bitmap
        self.children = children
        h = hashlib.sha256()
        h.update(f"B:{bitmap:08x}".encode("ascii"))
        for child in children:
            h.update(child.hash.encode("ascii"))
        self.hash = h.hexdigest()

    def index_of(self, bit: int) -> int:
        """Position of `bit` among the set bits below it (popcount)."""
        return bin(self.bitmap & (bit - 1)).count("1")


_EMPTY_BRANCH = _Branch(0, ())


def _set(node: Any, depth: int, leaf: _Leaf) -> Tuple[Any, bool]:
    """Return (new node, added) with `leaf` inserted; path-copying."""
    if isinstance(node, _Leaf):
        if node.key == leaf.key:
            return (node if node.hash == leaf.hash else leaf), False
        if node.khash == leaf.khash:
            raise ValueError(
                f"sha256 collision between keys {node.key!r} and {leaf.key!r}"
            )
        # Two leaves competing for one slot: grow a branch until they part.
        return _pair(node, leaf, depth), True
    if depth >= MAX_DEPTH:  # pragma: no cover — unreachable below a collision
        raise ValueError("key hash exhausted")
    bit = 1 << _chunk(leaf.khash, depth)
    idx = node.index_of(bit)
    if node.bitmap & bit:
        child, added = _set(node.children[idx], depth + 1, leaf)
        if child is node.children[idx]:
            return node, False
        children = node.children[:idx] + (child,) + node.children[idx + 1 :]
        return _Branch(node.bitmap, children), added
    children = node.children[:idx] + (leaf,) + node.children[idx:]
    return _Branch(node.bitmap | bit, children), True


def _pair(a: _Leaf, b: _Leaf, depth: int) -> _Branch:
    ca, cb = _chunk(a.khash, depth), _chunk(b.khash, depth)
    if ca == cb:
        return _Branch(1 << ca, (_pair(a, b, depth + 1),))
    if ca < cb:
        return _Branch((1 << ca) | (1 << cb), (a, b))
    return _Branch((1 << ca) | (1 << cb), (b, a))


def _delete(node: Any, depth: int, key: str, khash: int) -> Tuple[Any, bool]:
    """Return (new node or None, removed). Canonicalises on the way up."""
    if isinstance(node, _Leaf):
        return (None, True) if node.key == key else (node, False)
    bit = 1 << _chunk(khash, depth)
    if not node.bitmap & bit:
        return node, False
    idx = node.index_of(bit)
    child, removed = _delete(node.children[idx], depth + 1, key, khash)
    if not removed:
        return node, False
    if child is None:
        bitmap = node.bitmap & ~bit
        children = node.children[:idx] + node.children[idx + 1 :]
    else:
        bitmap = node.bitmap
        children = node.children[:idx] + (child,) + node.children[idx + 1 :]
    if depth > 0:
        if not children:
            return None, True
        if len(children) == 1 and isinstance(children[0], _Leaf):
            return children[0], True  # lift the lone leaf: canonical form
    return _Branch(bitmap, children), True


def _get(node: Any, depth: int, key: str, khash: int) -> Tuple[bool, Any]:
    while True:
        if isinstance(node, _Leaf):
            return (True, node.value) if node.key == key else (False, None)
        bit = 1 << _chunk(khash, depth)
        if not node.bitmap & bit:
            return False, None
        node = node.children[node.index_of(bit)]
        depth += 1


def _items(node: Any) -> Iterator[Tuple[str, Any]]:
    if isinstance(node, _Leaf):
        yield node.key, node.value
        return
    for child in node.children:
        yield from _items(child)


class PMap:
    """An immutable map. Every mutator returns a new `PMap`; the old one is
    untouched and shares every unchanged subtree with the new one."""

    __slots__ = ("_root", "_size")

    def __init__(self, root: _Branch = _EMPTY_BRANCH, size: int = 0) -> None:
        self._root = root
        self._size = size

    # -- reads ---------------------------------------------------------------

    def get(self, key: str, default: Any = None) -> Any:
        found, value = _get(self._root, 0, key, key_hash(key))
        return value if found else default

    def __contains__(self, key: object) -> bool:
        if not isinstance(key, str):
            return False
        return _get(self._root, 0, key, key_hash(key))[0]

    def __getitem__(self, key: str) -> Any:
        found, value = _get(self._root, 0, key, key_hash(key))
        if not found:
            raise KeyError(key)
        return value

    def __len__(self) -> int:
        return self._size

    def __iter__(self) -> Iterator[str]:
        for key, _ in _items(self._root):
            yield key

    def items(self) -> Iterator[Tuple[str, Any]]:
        return _items(self._root)

    def keys(self) -> Iterator[str]:
        return iter(self)

    @property
    def root_hash(self) -> str:
        """Merkle hash of the whole mapping. Equal iff the mappings are equal
        (up to sha256), whatever their histories."""
        return self._root.hash

    def __eq__(self, other: object) -> bool:
        return isinstance(other, PMap) and self._root.hash == other._root.hash

    def __hash__(self) -> int:
        return hash(self._root.hash)

    def __repr__(self) -> str:
        return f"PMap(size={self._size}, root={self._root.hash[:12]})"

    # -- writes (all return a new PMap) --------------------------------------

    def set(self, key: str, value: Any) -> "PMap":
        leaf = _Leaf(key, value, key_hash(key))
        root, added = _set(self._root, 0, leaf)
        if root is self._root:
            return self
        return PMap(root, self._size + (1 if added else 0))

    def delete(self, key: str) -> "PMap":
        root, removed = _delete(self._root, 0, key, key_hash(key))
        if not removed:
            return self
        return PMap(root if root is not None else _EMPTY_BRANCH, self._size - 1)

    def update(self, entries: Dict[str, Any]) -> "PMap":
        pm = self
        for key, value in entries.items():
            pm = pm.set(key, value)
        return pm

    @classmethod
    def of(cls, entries: Optional[Dict[str, Any]] = None) -> "PMap":
        return cls().update(entries or {})


# --- diff -------------------------------------------------------------------


class Diff:
    """What changed between two maps. `changed` maps key → (old, new)."""

    __slots__ = ("added", "removed", "changed")

    def __init__(self) -> None:
        self.added: Dict[str, Any] = {}
        self.removed: Dict[str, Any] = {}
        self.changed: Dict[str, Tuple[Any, Any]] = {}

    @property
    def keys(self) -> frozenset:
        return frozenset(self.added) | frozenset(self.removed) | frozenset(self.changed)

    def __bool__(self) -> bool:
        return bool(self.added or self.removed or self.changed)

    def __repr__(self) -> str:
        return (
            f"Diff(+{len(self.added)} -{len(self.removed)} ~{len(self.changed)})"
        )


def diff(old: PMap, new: PMap) -> Diff:
    """Structural diff. Shared subtrees (same object, or same Merkle hash) are
    skipped without descent, so the cost is proportional to the change."""
    out = Diff()
    _diff(old._root, new._root, out)
    return out


def _diff(a: Any, b: Any, out: Diff) -> None:
    if a is b or a.hash == b.hash:
        return
    if isinstance(a, _Leaf) or isinstance(b, _Leaf):
        # At least one side is a single entry: fall back to item comparison
        # over the (small) subtrees.
        left = dict(_items(a))
        right = dict(_items(b))
        for k, v in left.items():
            if k not in right:
                out.removed[k] = v
            elif right[k] != v:
                out.changed[k] = (v, right[k])
        for k, v in right.items():
            if k not in left:
                out.added[k] = v
        return
    bits = a.bitmap | b.bitmap
    bit = 1
    while bit <= bits:
        if bits & bit:
            ina, inb = bool(a.bitmap & bit), bool(b.bitmap & bit)
            if ina and inb:
                _diff(a.children[a.index_of(bit)], b.children[b.index_of(bit)], out)
            elif ina:
                for k, v in _items(a.children[a.index_of(bit)]):
                    out.removed[k] = v
            else:
                for k, v in _items(b.children[b.index_of(bit)]):
                    out.added[k] = v
        bit <<= 1


__all__ = ["Diff", "PMap", "diff", "key_hash"]
