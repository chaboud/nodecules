"""Strips as nodes, the small form: a strip is one node whose data is the
list of its elements, appended copy-on-write.

This is the right shape for a chat log, an input log, or a few thousand
observations — anything a demo appends to and reads whole, by window, or
by count. It is the wrong shape for hours of audio frames, where every
element should be its own node. Both are "a strip"; this one is the
polymer written as a single molecule.

Elements are JSON values. A strip node's kind is `strip`; readers use the
ordinary access patterns (`generation.read`): `All` is the list, `Latest`
the last element, `Range` a filter by a time field (relative to the reader
or to the head), `Ordinal` a slice by position (`last(n)`, `previous(n)`).

**Releasing old elements, and the floor.** `strip_trim` keeps the last
`keep_last` elements and records how many were released, the strip's
*floor*. Positions stay absolute, so element 1,000 is element 1,000 after
the first 900 are gone. The data of a trimmed strip is
`{"strip_floor": k, "elements": [...]}`; an untrimmed strip stays a plain
list, so nothing that exists changes. A read that needs a released element
is lost and names the floor: an `All` read always (it needs every element),
an ordinal read that starts below the floor, a time read whose window
starts before the earliest retained element. A read inside what is
retained is unaffected. This is the retention floor of REFERENCE-MODEL §17
in its cheap form: it comes before any policy that decides when to trim.
"""

from __future__ import annotations

from typing import Any, List, Optional, Tuple

from .store import Manifest, Node, Store

STRIP_KIND = "strip"


def strip_view(data: Any) -> Tuple[int, List[Any]]:
    """(floor, retained elements) of a strip's data: a plain list has floor 0;
    a trimmed strip carries its floor."""
    if isinstance(data, list):
        return 0, data
    if isinstance(data, dict) and "strip_floor" in data and isinstance(data.get("elements"), list):
        return int(data["strip_floor"]), data["elements"]
    raise ValueError("not a strip")


def _strip_data(floor: int, elements: List[Any]) -> Any:
    return list(elements) if floor == 0 else {"strip_floor": floor, "elements": list(elements)}


def strip_read(store: Store, manifest: Manifest, name: str) -> List[Any]:
    """The strip's retained elements as bound by `manifest`; empty if unbound."""
    node = store.get(manifest, name)
    if not isinstance(node, Node) or node.data is None:
        return []
    try:
        return list(strip_view(node.data)[1])
    except ValueError:
        raise ValueError(f"{name!r} is not a strip") from None


def strip_floor(store: Store, manifest: Manifest, name: str) -> int:
    """How many elements the strip has released (0 if it never trimmed)."""
    node = store.get(manifest, name)
    if not isinstance(node, Node) or node.data is None:
        return 0
    return strip_view(node.data)[0]


def strip_append(store: Store, scope: str, name: str, *elements: Any, author: str = "") -> Manifest:
    """Append elements to a strip in one commit, creating it if needed.
    Old manifests still see the old strip; that is the point."""
    tx = store.transaction(scope, author=author)
    node = store.get(tx.base, name)
    floor, current = (0, []) if not isinstance(node, Node) or node.data is None else strip_view(node.data)
    tx.put(Node(id=name, kind=STRIP_KIND, scope=scope, data=_strip_data(floor, [*current, *elements])))
    return tx.commit(note=f"append {len(elements)} to {name}")


def strip_trim(store: Store, scope: str, name: str, *, keep_last: int, author: str = "") -> Manifest:
    """Release all but the last `keep_last` elements, recording the floor.
    Old manifests still see every element; the release is a new version."""
    if keep_last < 1:
        raise ValueError("keep_last must be at least 1; a strip with nothing retained has no head")
    tx = store.transaction(scope, author=author)
    node = store.get(tx.base, name)
    if not isinstance(node, Node) or node.data is None:
        raise ValueError(f"{name!r} is not bound in {scope!r}")
    floor, current = strip_view(node.data)
    if len(current) <= keep_last:
        return tx.base
    released = len(current) - keep_last
    tx.put(Node(id=name, kind=STRIP_KIND, scope=scope, data=_strip_data(floor + released, current[released:])))
    return tx.commit(note=f"release {released} from {name} (floor {floor + released})")


def strip_len(store: Store, manifest: Manifest, name: str) -> int:
    """The strip's length counting released elements: the next element's position."""
    node = store.get(manifest, name)
    if not isinstance(node, Node) or node.data is None:
        return 0
    floor, elements = strip_view(node.data)
    return floor + len(elements)


__all__ = ["STRIP_KIND", "strip_append", "strip_floor", "strip_len", "strip_read", "strip_trim", "strip_view"]
