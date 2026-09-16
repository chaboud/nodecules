"""Activation — which version is live, and markers that name versions.

Behavior is data with a factory default, an active pointer, a version
history, and a one-step rollback (the vault's D-7; keyhole's edict). On
the store those are not new mechanisms:

- The **version history** is the manifest chain.
- A **marker** is a node (`markers/<label>`) that names a manifest by hash:
  `factory`, `last-known-good`, `before-the-demo`. Markers are decoration:
  functional nodes never point at them, so labelling a version never
  changes what it is.
- **Activating** a version is a *forward* commit whose entries are the
  target manifest's, bound by hash without loading a single body, with
  `activated` on the new manifest naming what was restored. History stays
  a straight line and shows the rollback; replicas take it as an ordinary
  commit. The head never moves backward, so nothing that happened is lost
  and a later activation can undo this one.
- Markers themselves are carried across an activation: restoring the
  factory version does not forget that there is a factory version.

The **active pointer** is therefore the scope's head, and "pin" is a
marker plus an activation. Nothing here is specific to behavior
templates; it works for any scope, which is the point.
"""

from __future__ import annotations

from typing import Dict, Optional

from .store import Manifest, Node, Store

MARKER_KIND = "marker"
MARKER_PREFIX = "markers/"


def marker_id(label: str) -> str:
    return f"{MARKER_PREFIX}{label}"


def mark(store: Store, scope: str, label: str, manifest: Optional[Manifest] = None, *, note: str = "", author: str = "") -> Manifest:
    """Label a manifest (default: the current head) so it can be activated by
    name later. Returns the manifest that carries the marker."""
    target = manifest if manifest is not None else store.current(scope)
    if store.manifest(target.content_hash()) is None:
        raise ValueError("cannot mark a manifest the store does not know")
    tx = store.transaction(scope, author=author)
    tx.put(Node(id=marker_id(label), kind=MARKER_KIND, scope=scope, data={"label": label, "manifest": target.content_hash(), "seq": target.seq, "note": note}))
    return tx.commit(note=f"mark {label} -> seq {target.seq}")


def marks(store: Store, manifest: Manifest) -> Dict[str, str]:
    """label -> manifest hash, as bound by `manifest`."""
    out: Dict[str, str] = {}
    for name, h in manifest.entries():
        if name.startswith(MARKER_PREFIX):
            node = store.get_by_hash(h)
            if node is not None and node.data is not None:
                out[node.data["label"]] = node.data["manifest"]
    return out


def resolve(store: Store, scope: str, target: str) -> Manifest:
    """A manifest by label or by hash."""
    labelled = marks(store, store.current(scope)).get(target)
    m = store.manifest(labelled or target)
    if m is None:
        raise ValueError(f"{target!r} is neither a marker on {scope!r} nor a manifest this store knows")
    if m.scope != scope:
        raise ValueError(f"{target!r} belongs to {m.scope!r}, not {scope!r}")
    return m


def activate(store: Store, scope: str, target: str, *, author: str = "", note: str = "") -> Manifest:
    """Make `target` (a marker label or a manifest hash) the live content of
    `scope` by a forward commit. Markers on the current head are kept."""
    goal = resolve(store, scope, target)
    head = store.current(scope)

    def functional(m: Manifest) -> Dict[str, str]:
        return {n: h for n, h in m.entries() if not n.startswith(MARKER_PREFIX)}

    if functional(goal) == functional(head):
        return head  # already live, whatever the history says
    tx = store.transaction(scope, author=author)
    goal_names = set()
    for name, h in goal.entries():
        if name.startswith(MARKER_PREFIX):
            continue
        goal_names.add(name)
        if head.hash_of(name) != h:
            tx.bind(name, h)
    for name in head.ids():
        if name.startswith(MARKER_PREFIX) or name in goal_names:
            continue
        tx.delete(name)
    tx.activated = goal.content_hash()
    return tx.commit(note=note or f"activate {target} (seq {goal.seq})")


__all__ = ["MARKER_KIND", "MARKER_PREFIX", "activate", "mark", "marker_id", "marks", "resolve"]
