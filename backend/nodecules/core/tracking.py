"""Tracking — what happened, in order, outside the node store.

REFERENCE-MODEL §21: diagnostics live outside the node store, per scope.
A `Tracker` is an append-only log of events: every commit and head move
(it listens to the store), every production (the generator reports to
it), and whatever a consumer records. Events carry a sequence number, an
optional tick from an injected clock (never a wall clock read here), the
scope and node they concern, and a detail dict. A sink, such as
`JsonlSink`, gets every event as it happens, which is how a log survives
the process.

`lineage` answers "where did this come from": the node, its receipt, and
recursively the inputs the receipt names, as bound by a manifest. It
reads envelopes, so it works for pruned bodies too.
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any, Callable, Dict, Iterator, List, Optional

from pydantic import BaseModel, ConfigDict

from .store import Manifest, Node, Store, envelope_id


class Event(BaseModel):
    model_config = ConfigDict(frozen=True)

    seq: int
    kind: str  # "commit", "head", "produce.cooked", "produce.cache-hit", "produce.failed", "produce.lost", or a consumer's own
    scope: str
    node: Optional[str] = None
    at: Optional[int] = None  # ticks on the tracker's timeline, if a clock was given
    detail: Dict[str, Any] = {}


class JsonlSink:
    """Append every event as one JSON line."""

    def __init__(self, path: str | Path) -> None:
        self.path = Path(path)
        self.path.parent.mkdir(parents=True, exist_ok=True)

    def __call__(self, event: Event) -> None:
        with self.path.open("a", encoding="utf-8") as f:
            f.write(json.dumps(event.model_dump(mode="json"), sort_keys=True) + "\n")


class Tracker:
    def __init__(self, *, clock: Optional[Callable[[], int]] = None, timeline: str = "", sink: Optional[Callable[[Event], None]] = None) -> None:
        self._events: List[Event] = []
        self._clock = clock
        self.timeline = timeline
        self._sinks: List[Callable[[Event], None]] = [sink] if sink else []

    def add_sink(self, sink: Callable[[Event], None]) -> None:
        self._sinks.append(sink)

    def record(self, kind: str, scope: str, node: Optional[str] = None, **detail: Any) -> Event:
        ev = Event(seq=len(self._events), kind=kind, scope=scope, node=node, at=self._clock() if self._clock else None, detail={k: v for k, v in detail.items() if v is not None and v != [] and v != 0 and v != ""} if kind.startswith("produce.") else dict(detail))
        self._events.append(ev)
        for s in self._sinks:
            s(ev)
        return ev

    def events(self, *, scope: Optional[str] = None, kind: Optional[str] = None, node: Optional[str] = None) -> List[Event]:
        return [
            e
            for e in self._events
            if (scope is None or e.scope == scope) and (kind is None or e.kind == kind or e.kind.startswith(kind + ".")) and (node is None or e.node == node)
        ]

    def __len__(self) -> int:
        return len(self._events)

    # -- listening to a store ------------------------------------------------------------

    def attach(self, store: Store) -> None:
        """Record every commit and head move on the store."""

        def listen(event: str, m: Manifest) -> None:
            self.record(event, m.scope, None, seq=m.seq, author=m.author, note=m.note, manifest=m.content_hash(), parent=m.parent, activated=m.activated, merge_parent=m.merge_parent)

        store.listeners.append(listen)


def lineage(store: Store, manifest: Manifest, node_id: str, *, depth: int = 8) -> Dict[str, Any]:
    """The provenance tree of a node as bound by `manifest`: its identity,
    its receipt if it has one, and its inputs, recursively."""
    h = manifest.hash_of(node_id)
    out: Dict[str, Any] = {"id": node_id, "content_hash": h}
    if h is None:
        out["missing"] = True
        return out
    got = store.get(manifest, node_id)
    out["kind"] = getattr(got, "kind", None)
    out["residency"] = store.residency(h)
    env = store.get(manifest, envelope_id(node_id))
    if isinstance(env, Node) and env.data is not None:
        d = env.data
        out["receipt"] = {k: d.get(k) for k in ("outcome", "reproducibility", "measured", "cache_key", "falsified_determinism") if k in d}
        out["recipe"] = d.get("recipe")
        if depth > 0:
            out["inputs"] = []
            for ref, ih in sorted((d.get("inputs") or {}).items()):
                scope, _, name = ref.partition(":")
                m = manifest if scope == manifest.scope else store.current(scope)
                out["inputs"].append(lineage(store, m, name, depth=depth - 1))
    return out


__all__ = ["Event", "JsonlSink", "Tracker", "lineage"]
