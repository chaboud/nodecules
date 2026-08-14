"""Annotation primitives — `AnnotationNode`, `AnnotationRef`, index.

Per TEMPORALITY.md: an annotation is a first-class node in the graph with
its own `node_id`, `emission_ms`, `target_window: TimeRange`, and a
`payload`. Its content hash participates in the cache key of every
downstream node whose window intersects `target_window`. Adding an
annotation invalidates exactly the affected subgraph; removing the same
annotation invalidates the same subgraph.

This module defines the base types. Concrete annotation payloads (speaker
identity, transcript correction, tag, freeform note) live in consumer
packages — stenota owns meeting-specific annotations; nodecules stays
domain-agnostic.
"""

from __future__ import annotations

import hashlib
import json
from typing import Any, Iterable, Optional

from pydantic import BaseModel, ConfigDict, Field

from .time import TimeRange


def _canonical(obj: Any) -> str:
    return json.dumps(obj, sort_keys=True, separators=(",", ":"))


def _sha256(data: str) -> str:
    return hashlib.sha256(data.encode("utf-8")).hexdigest()


class AnnotationNode(BaseModel):
    """Base class for user-emitted annotations.

    The `content_hash` is derived from `(node_id, emission_ms,
    target_window, payload)` — deterministic, so the same annotation
    always produces the same cache-key contribution.
    """

    model_config = ConfigDict(frozen=True)

    node_id: str
    emission_ms: int = Field(ge=0)
    target_window: TimeRange
    payload: dict[str, Any] = Field(default_factory=dict)

    def content_hash(self) -> str:
        payload = {
            "node_id": self.node_id,
            "emission_ms": self.emission_ms,
            "target_window": self.target_window.model_dump(),
            "payload": self.payload,
        }
        return _sha256(_canonical(payload))


class AnnotationRef(BaseModel):
    """Lightweight reference used inside cache keys + provenance trails."""

    model_config = ConfigDict(frozen=True)

    annotation_id: str
    target_window: TimeRange
    content_hash: str

    @classmethod
    def from_node(cls, node: AnnotationNode) -> "AnnotationRef":
        return cls(
            annotation_id=node.node_id,
            target_window=node.target_window,
            content_hash=node.content_hash(),
        )


class AnnotationIndex:
    """Maintains annotations by id + fast window-intersection lookup.

    Implementation is intentionally simple — a flat list of
    `AnnotationNode` plus a dict for O(1) id lookup. Queries are O(N)
    scans. For the sizes we expect (annotations per meeting, not
    annotations per year), that's fine. A smarter interval tree is a
    later optimization; the public API won't change.
    """

    def __init__(self) -> None:
        self._by_id: dict[str, AnnotationNode] = {}

    def __len__(self) -> int:
        return len(self._by_id)

    def __contains__(self, annotation_id: str) -> bool:
        return annotation_id in self._by_id

    def add(self, node: AnnotationNode) -> None:
        """Add or replace an annotation. Replacement changes its hash."""
        self._by_id[node.node_id] = node

    def remove(self, annotation_id: str) -> Optional[AnnotationNode]:
        """Remove an annotation. Returns the removed node or `None`."""
        return self._by_id.pop(annotation_id, None)

    def get(self, annotation_id: str) -> Optional[AnnotationNode]:
        return self._by_id.get(annotation_id)

    def all(self) -> list[AnnotationNode]:
        return list(self._by_id.values())

    def annotations_in_window(self, window: TimeRange) -> list[AnnotationRef]:
        """Return refs to every annotation whose target_window intersects `window`."""
        return [
            AnnotationRef.from_node(n)
            for n in self._by_id.values()
            if n.target_window.intersects(window)
        ]

    def annotation_hash_for_window(self, window: TimeRange) -> Optional[str]:
        """Canonical hash over annotations intersecting `window`.

        Returns `None` when there are no annotations intersecting the
        window — so a node that *reads annotations* but has none in this
        window gets a deterministic, empty-set hash (not `None`). The
        `None` return is reserved for nodes that do not read annotations
        at all. Callers distinguish the two cases.
        """
        refs = self.annotations_in_window(window)
        if not refs:
            # "No annotations in this window" is a real state distinct
            # from "this node doesn't read annotations." We return the
            # empty-list hash, not `None`. Callers that don't want to
            # participate in annotation invalidation pass `None` to the
            # cache-key builder themselves.
            return _sha256(_canonical([]))
        ordered = sorted(ref.content_hash for ref in refs)
        return _sha256(_canonical(ordered))


def content_hashes_for_window(
    index: AnnotationIndex, window: TimeRange
) -> list[str]:
    """Helper: return only the content hashes, sorted, for cache-key use."""
    refs = index.annotations_in_window(window)
    return sorted(ref.content_hash for ref in refs)


__all__ = [
    "AnnotationIndex",
    "AnnotationNode",
    "AnnotationRef",
    "content_hashes_for_window",
]
