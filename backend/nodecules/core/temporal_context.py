"""`ChunkedContext` — execution context for temporal/windowed nodes.

Extends `ExecutionContext` with the fields a windowed node needs to behave
deterministically: the current `TimeRange` being processed, the
`TimeSource` driving the scheduler, and lookup methods that respect the
window boundary.

Static nodes never see one of these (they receive a plain
`ExecutionContext`). Temporal nodes receive `ChunkedContext` and can
downcast from `ExecutionContext` via `isinstance`.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, List, Optional

from .annotations import AnnotationIndex, AnnotationRef
from .time import TimeRange, TimeSource
from .types import ExecutionContext


@dataclass
class ChunkedContext(ExecutionContext):
    """ExecutionContext carrying window + time-source info for temporal nodes.

    New fields are optional so the dataclass can be constructed without
    them during gradual migration of static code paths. Scheduler code
    populates them for real windowed executions.
    """
    # All new fields have defaults so subclassing a dataclass with
    # defaulted parent fields stays legal.
    current_window: Optional[TimeRange] = None
    time_source: Optional[TimeSource] = None
    annotation_index: Optional[AnnotationIndex] = None

    def get_inputs_in_window(
        self,
        node_id: str,
        port: str,
        window: TimeRange,
    ) -> List[Any]:
        """Return upstream values whose `TimeRange` intersects `window`.

        In PR-n1 this is a placeholder that delegates to the static
        `get_input_value` for backwards compatibility; the scheduler in
        PR-n3 replaces this with a real window-aware lookup that reads
        from the sidecar JSONL streams rather than in-memory node
        outputs. Long meetings must not materialize all upstream data.
        """
        value = self.get_input_value(node_id, port)
        return [value] if value is not None else []

    def get_annotations_in_window(
        self,
        window: TimeRange,
    ) -> List[AnnotationRef]:
        """Return annotations whose `target_window` intersects `window`.

        Empty list when there is no `annotation_index` bound (static path
        or a subgraph that hasn't been wired to one).
        """
        if self.annotation_index is None:
            return []
        return self.annotation_index.annotations_in_window(window)

    def annotation_hash_for_window(self, window: TimeRange) -> Optional[str]:
        """Canonical hash over annotations intersecting `window`.

        Returns `None` when there is no annotation index bound — the
        cache key should then be built with `annotation_hash=None`,
        reproducing static-node behavior. When an index IS bound the
        return value is the empty-set hash for "no annotations here"
        (distinct from `None`), so a node that reads annotations pays
        the full cache-invalidation cost whenever annotations in its
        window change.
        """
        if self.annotation_index is None:
            return None
        return self.annotation_index.annotation_hash_for_window(window)


__all__ = ["ChunkedContext"]
