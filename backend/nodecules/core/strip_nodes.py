"""Strips as nodes, the small form: a strip is one node whose data is the
list of its elements, appended copy-on-write.

This is the right shape for a chat log, an input log, or a few hundred
observations — anything a demo appends to and reads whole or by range.
It is the wrong shape for hours of audio frames, where every element
should be its own node and readers use the relative and range patterns
(PR-r1's cut, queued to return with their retention floor). Both are
"a strip"; this one is the polymer written as a single molecule.

Elements are JSON values. A strip node's kind is `strip`; readers use the
ordinary access patterns: `All` is the list, `Latest` the last element,
`Range` a filter by a time field, through `generation.select`.
"""

from __future__ import annotations

from typing import Any, List, Optional

from .store import Manifest, Node, Store

STRIP_KIND = "strip"


def strip_read(store: Store, manifest: Manifest, name: str) -> List[Any]:
    """The strip's elements as bound by `manifest`; empty if unbound."""
    node = store.get(manifest, name)
    if not isinstance(node, Node) or node.data is None:
        return []
    if not isinstance(node.data, list):
        raise ValueError(f"{name!r} is not a strip")
    return list(node.data)


def strip_append(store: Store, scope: str, name: str, *elements: Any, author: str = "") -> Manifest:
    """Append elements to a strip in one commit, creating it if needed.
    Old manifests still see the old strip; that is the point."""
    tx = store.transaction(scope, author=author)
    current = strip_read(store, tx.base, name)
    tx.put(Node(id=name, kind=STRIP_KIND, scope=scope, data=[*current, *elements]))
    return tx.commit(note=f"append {len(elements)} to {name}")


def strip_len(store: Store, manifest: Manifest, name: str) -> int:
    return len(strip_read(store, manifest, name))


__all__ = ["STRIP_KIND", "strip_append", "strip_len", "strip_read"]
