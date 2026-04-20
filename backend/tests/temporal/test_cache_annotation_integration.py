"""Integration: annotation invalidation only touches intersecting windows.

The TEMPORALITY.md promise is that adding an annotation whose
`target_window` intersects window `W` invalidates cache entries for
nodes-at-`W` whose key includes `annotation_hash`, and ONLY those. Other
windows stay cached.

We exercise that promise end-to-end: filesystem cache backend +
`AnnotationIndex` + the `build_cache_key` helper, mirroring the shape a
real scheduler will use.
"""

from __future__ import annotations

from pathlib import Path

from nodecules.core.annotations import (
    AnnotationIndex,
    AnnotationNode,
    content_hashes_for_window,
)
from nodecules.core.node_cache import FilesystemNodeCache, build_cache_key
from nodecules.core.time import TimeRange


def _mk_key(
    window: TimeRange,
    *,
    index: AnnotationIndex,
    reads_annotations: bool,
):
    """Build the cache key a windowed node would use, with or without
    annotation participation."""
    hashes = (
        content_hashes_for_window(index, window) if reads_annotations else None
    )
    return build_cache_key(
        node_type="summarizer.l2",
        node_version="0.1.0",
        params={"temperature": 0.1},
        inputs=(f"turns@{window.start_ms}-{window.end_ms}",),
        window=window,
        annotation_content_hashes=hashes,
    )


class TestAnnotationInvalidation:
    def test_annotation_smudges_only_affected_window(self, tmp_path: Path) -> None:
        cache = FilesystemNodeCache(tmp_path)
        index = AnnotationIndex()

        window_a = TimeRange(start_ms=0, end_ms=30_000)
        window_b = TimeRange(start_ms=30_000, end_ms=60_000)
        window_c = TimeRange(start_ms=60_000, end_ms=90_000)

        # Initial run — summarizer is annotation-aware; every window
        # caches with the empty-set annotation hash.
        ka_v1 = _mk_key(window_a, index=index, reads_annotations=True)
        kb_v1 = _mk_key(window_b, index=index, reads_annotations=True)
        kc_v1 = _mk_key(window_c, index=index, reads_annotations=True)
        cache.put(ka_v1, {"claims": ["A0"]})
        cache.put(kb_v1, {"claims": ["B0"]})
        cache.put(kc_v1, {"claims": ["C0"]})
        assert cache.has(ka_v1)
        assert cache.has(kb_v1)
        assert cache.has(kc_v1)

        # Annotation lands in window_b.
        index.add(
            AnnotationNode(
                node_id="ann-1",
                emission_ms=45_000,
                target_window=TimeRange(start_ms=40_000, end_ms=50_000),
                payload={"correction": "SPEAKER_03 is Alice"},
            )
        )

        # Rebuild keys after the annotation change.
        ka_v2 = _mk_key(window_a, index=index, reads_annotations=True)
        kb_v2 = _mk_key(window_b, index=index, reads_annotations=True)
        kc_v2 = _mk_key(window_c, index=index, reads_annotations=True)

        # Window A and C keys are UNCHANGED (no annotation intersects them).
        assert ka_v1.digest() == ka_v2.digest()
        assert cache.has(ka_v2)
        assert cache.get(ka_v2) == {"claims": ["A0"]}

        assert kc_v1.digest() == kc_v2.digest()
        assert cache.has(kc_v2)

        # Window B's key has changed — the old cached entry no longer
        # corresponds to the current (inputs + annotations) tuple. The
        # old blob is still on disk, but the NEW key returns a miss.
        assert kb_v1.digest() != kb_v2.digest()
        assert not cache.has(kb_v2)
        assert cache.get(kb_v2) is None

    def test_removing_annotation_restores_key(self, tmp_path: Path) -> None:
        """Undo/redo: removing an annotation brings back the original key."""
        cache = FilesystemNodeCache(tmp_path)
        index = AnnotationIndex()
        window = TimeRange(start_ms=0, end_ms=30_000)

        # Pre-annotation key + cached value.
        k0 = _mk_key(window, index=index, reads_annotations=True)
        cache.put(k0, "pre-annotation")

        # Add annotation → key changes.
        index.add(
            AnnotationNode(
                node_id="ann-1",
                emission_ms=10_000,
                target_window=TimeRange(start_ms=5_000, end_ms=15_000),
                payload={"v": 1},
            )
        )
        k1 = _mk_key(window, index=index, reads_annotations=True)
        assert k0.digest() != k1.digest()

        # Remove it → key returns to k0 shape.
        index.remove("ann-1")
        k2 = _mk_key(window, index=index, reads_annotations=True)
        assert k0.digest() == k2.digest()
        assert cache.get(k2) == "pre-annotation"

    def test_annotation_unaware_node_stays_cached(self, tmp_path: Path) -> None:
        """A node that doesn't read annotations must not re-run just because
        an annotation landed somewhere in the graph.

        Regression guard for the promise that `annotation_hash=None` stays
        `None` for static or annotation-ignoring nodes.
        """
        cache = FilesystemNodeCache(tmp_path)
        index = AnnotationIndex()
        window = TimeRange(start_ms=0, end_ms=30_000)

        k_pre = _mk_key(window, index=index, reads_annotations=False)
        cache.put(k_pre, "value")

        # Annotation lands INSIDE the same window.
        index.add(
            AnnotationNode(
                node_id="ann-1",
                emission_ms=10_000,
                target_window=TimeRange(start_ms=5_000, end_ms=15_000),
                payload={"v": 1},
            )
        )

        k_post = _mk_key(window, index=index, reads_annotations=False)
        # Same key, same cached value — the node never opted in.
        assert k_pre.digest() == k_post.digest()
        assert cache.get(k_post) == "value"

    def test_two_annotations_in_same_window_both_contribute(
        self, tmp_path: Path
    ) -> None:
        """ARCHITECTURE.md promise: annotations compose — two in the same
        window both contribute to the downstream cache key."""
        index = AnnotationIndex()
        window = TimeRange(start_ms=0, end_ms=30_000)

        index.add(
            AnnotationNode(
                node_id="a",
                emission_ms=10_000,
                target_window=TimeRange(start_ms=5_000, end_ms=15_000),
                payload={"v": "first"},
            )
        )
        with_one = _mk_key(window, index=index, reads_annotations=True)

        index.add(
            AnnotationNode(
                node_id="b",
                emission_ms=20_000,
                target_window=TimeRange(start_ms=18_000, end_ms=25_000),
                payload={"v": "second"},
            )
        )
        with_two = _mk_key(window, index=index, reads_annotations=True)

        assert with_one.digest() != with_two.digest()
