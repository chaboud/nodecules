"""Tests for `AnnotationNode`, `AnnotationRef`, and `AnnotationIndex`."""

from __future__ import annotations

from nodecules.core.annotations import (
    AnnotationIndex,
    AnnotationNode,
    AnnotationRef,
    content_hashes_for_window,
)
from nodecules.core.time import TimeRange


def _ann(
    node_id: str,
    start_ms: int,
    end_ms: int,
    *,
    emission_ms: int = 0,
    payload: dict | None = None,
) -> AnnotationNode:
    return AnnotationNode(
        node_id=node_id,
        emission_ms=emission_ms,
        target_window=TimeRange(start_ms=start_ms, end_ms=end_ms),
        payload=payload or {},
    )


# --- AnnotationNode + content hashing ------------------------------------


class TestContentHash:
    def test_equal_nodes_hash_equal(self) -> None:
        a = _ann("a-1", 0, 1_000, payload={"speaker": "SPEAKER_03", "name": "Alice"})
        b = _ann("a-1", 0, 1_000, payload={"speaker": "SPEAKER_03", "name": "Alice"})
        assert a.content_hash() == b.content_hash()

    def test_payload_change_changes_hash(self) -> None:
        a = _ann("a-1", 0, 1_000, payload={"speaker": "SPEAKER_03"})
        b = _ann("a-1", 0, 1_000, payload={"speaker": "SPEAKER_05"})
        assert a.content_hash() != b.content_hash()

    def test_window_change_changes_hash(self) -> None:
        a = _ann("a-1", 0, 1_000)
        b = _ann("a-1", 500, 1_500)
        assert a.content_hash() != b.content_hash()

    def test_payload_key_order_stable(self) -> None:
        a = _ann("a-1", 0, 1_000, payload={"x": 1, "y": 2})
        b = _ann("a-1", 0, 1_000, payload={"y": 2, "x": 1})
        assert a.content_hash() == b.content_hash()


# --- AnnotationRef -------------------------------------------------------


class TestAnnotationRef:
    def test_from_node_roundtrip(self) -> None:
        node = _ann("a-1", 100, 200, payload={"tag": "x"})
        ref = AnnotationRef.from_node(node)
        assert ref.annotation_id == "a-1"
        assert ref.target_window == TimeRange(start_ms=100, end_ms=200)
        assert ref.content_hash == node.content_hash()


# --- AnnotationIndex -----------------------------------------------------


class TestIndexBasics:
    def test_add_and_contains(self) -> None:
        idx = AnnotationIndex()
        idx.add(_ann("a-1", 0, 100))
        assert "a-1" in idx
        assert idx.get("a-1") is not None
        assert len(idx) == 1

    def test_remove(self) -> None:
        idx = AnnotationIndex()
        idx.add(_ann("a-1", 0, 100))
        removed = idx.remove("a-1")
        assert removed is not None
        assert "a-1" not in idx
        assert idx.remove("not-there") is None

    def test_replace_same_id(self) -> None:
        idx = AnnotationIndex()
        idx.add(_ann("a-1", 0, 100, payload={"v": 1}))
        idx.add(_ann("a-1", 0, 100, payload={"v": 2}))
        assert len(idx) == 1
        assert idx.get("a-1").payload == {"v": 2}  # type: ignore[union-attr]


class TestWindowLookup:
    def _index_with_three(self) -> AnnotationIndex:
        idx = AnnotationIndex()
        idx.add(_ann("early", 0, 1_000))
        idx.add(_ann("middle", 5_000, 6_000))
        idx.add(_ann("late", 10_000, 11_000))
        return idx

    def test_window_disjoint_from_all(self) -> None:
        idx = self._index_with_three()
        refs = idx.annotations_in_window(TimeRange(start_ms=20_000, end_ms=21_000))
        assert refs == []

    def test_window_covering_one(self) -> None:
        idx = self._index_with_three()
        refs = idx.annotations_in_window(TimeRange(start_ms=4_000, end_ms=7_000))
        assert {r.annotation_id for r in refs} == {"middle"}

    def test_window_covering_all(self) -> None:
        idx = self._index_with_three()
        refs = idx.annotations_in_window(TimeRange(start_ms=0, end_ms=20_000))
        assert {r.annotation_id for r in refs} == {"early", "middle", "late"}

    def test_touching_boundary_does_not_intersect(self) -> None:
        """Closed-open `TimeRange.intersects` — [0,1000) and [1000,2000) share no points."""
        idx = AnnotationIndex()
        idx.add(_ann("a", 0, 1_000))
        refs = idx.annotations_in_window(TimeRange(start_ms=1_000, end_ms=2_000))
        assert refs == []


class TestWindowHash:
    def test_empty_window_has_deterministic_nonnull_hash(self) -> None:
        """A node that reads annotations but has none in this window gets
        the empty-set hash — NOT `None` (which is reserved for nodes that
        don't participate in annotation invalidation at all)."""
        idx = AnnotationIndex()
        h = idx.annotation_hash_for_window(TimeRange(start_ms=0, end_ms=100))
        assert h is not None
        assert h != ""

    def test_single_annotation_changes_hash(self) -> None:
        idx = AnnotationIndex()
        empty_hash = idx.annotation_hash_for_window(
            TimeRange(start_ms=0, end_ms=1_000)
        )
        idx.add(_ann("a", 0, 500))
        one_hash = idx.annotation_hash_for_window(
            TimeRange(start_ms=0, end_ms=1_000)
        )
        assert empty_hash != one_hash

    def test_annotation_outside_window_does_not_affect_hash(self) -> None:
        """The promise in ARCHITECTURE.md: annotations only smudge the
        window they touch."""
        window = TimeRange(start_ms=0, end_ms=1_000)
        idx = AnnotationIndex()
        baseline = idx.annotation_hash_for_window(window)
        idx.add(_ann("distant", 50_000, 51_000))
        assert idx.annotation_hash_for_window(window) == baseline

    def test_annotation_order_irrelevant(self) -> None:
        """Two identical annotation sets produce the same hash regardless
        of insertion order — critical for deterministic cache behavior."""
        window = TimeRange(start_ms=0, end_ms=10_000)
        idx_a = AnnotationIndex()
        idx_a.add(_ann("x", 0, 1_000))
        idx_a.add(_ann("y", 5_000, 6_000))
        idx_b = AnnotationIndex()
        idx_b.add(_ann("y", 5_000, 6_000))
        idx_b.add(_ann("x", 0, 1_000))
        assert (
            idx_a.annotation_hash_for_window(window)
            == idx_b.annotation_hash_for_window(window)
        )

    def test_remove_then_add_same_hash(self) -> None:
        window = TimeRange(start_ms=0, end_ms=1_000)
        idx = AnnotationIndex()
        idx.add(_ann("a", 0, 500))
        with_a = idx.annotation_hash_for_window(window)
        idx.remove("a")
        idx.add(_ann("a", 0, 500))
        assert idx.annotation_hash_for_window(window) == with_a

    def test_changing_annotation_payload_changes_hash(self) -> None:
        """Undo/redo semantics: a payload edit invalidates the cache."""
        window = TimeRange(start_ms=0, end_ms=1_000)
        idx = AnnotationIndex()
        idx.add(_ann("a", 0, 500, payload={"v": 1}))
        before = idx.annotation_hash_for_window(window)
        idx.add(_ann("a", 0, 500, payload={"v": 2}))
        after = idx.annotation_hash_for_window(window)
        assert before != after


# --- Helpers -------------------------------------------------------------


class TestContentHashesForWindow:
    def test_sorted_output(self) -> None:
        idx = AnnotationIndex()
        idx.add(_ann("z-late", 0, 100))
        idx.add(_ann("a-early", 50, 150))
        window = TimeRange(start_ms=0, end_ms=200)
        hashes = content_hashes_for_window(idx, window)
        assert hashes == sorted(hashes)
