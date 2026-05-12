"""`ChunkedContext` — execution context for temporal/windowed nodes.

Extends `ExecutionContext` with the fields a windowed node needs to behave
deterministically: the current `TimeRange` being processed, the
`TimeSource` driving the scheduler, the optional annotation index,
(PR-n4) a strip registry + sidecar path for the strip pull API, and
(PR-n6) an optional subscription manager for the push API.

Static nodes never see one of these (they receive a plain
`ExecutionContext`). Temporal nodes receive `ChunkedContext` and can
downcast from `ExecutionContext` via `isinstance`.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any, List, Optional

from .annotations import AnnotationIndex, AnnotationRef
from .strips import StripRegistry, StripView
from .subscriptions import Subscription, SubscriptionManager, Visibility
from .time import TimeRange, TimeSource
from .types import DerivationPhase, ExecutionContext


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
    # PR-n4: strip registry + sidecar path together let `strip(name)` resolve
    # a strip view over the current sidecar. Either field may be None when
    # the caller doesn't need the strip API; both must be set (or sidecar
    # discoverable via `execution_inputs["sidecar_path"]`) when `strip()`
    # is called.
    strips: Optional[StripRegistry] = None
    sidecar: Optional[str] = None
    # PR-n6: subscription manager. Optional — nodes that don't use the push
    # API can leave this None.
    subscriptions: Optional[SubscriptionManager] = None

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

    # PR-n4: strip resolution -------------------------------------------------

    def strip(self, name: str) -> StripView:
        """Return a `StripView` for the named strip in the current sidecar.

        Looks up `name` in the registry, resolves the sidecar from
        `self.sidecar` (or falls back to `execution_inputs["sidecar_path"]`
        for compatibility with stenota's current pattern), and constructs
        the lazy view. Raises if no registry is bound or no sidecar is
        available.
        """
        if self.strips is None:
            raise RuntimeError(
                "ChunkedContext.strips is not set; bind a StripRegistry "
                "before calling strip()"
            )
        sc = self.sidecar
        if sc is None:
            sc = self.execution_inputs.get("sidecar_path")
        if not sc:
            raise RuntimeError(
                "no sidecar path available; set ctx.sidecar or "
                "execution_inputs['sidecar_path']"
            )
        spec = self.strips.get(name)
        return StripView(spec, sc)

    # PR-n6: subscription / publication --------------------------------------

    def subscribe(
        self,
        strip_name: str,
        visibility: Optional[Visibility] = None,
    ) -> Subscription:
        """Subscribe to a named strip.

        Returns an async-iterable `Subscription`. Default visibility is
        canonical-phase events at all times. Raises if no
        `SubscriptionManager` is bound on this context.
        """
        if self.subscriptions is None:
            raise RuntimeError(
                "ChunkedContext.subscriptions is not set; bind a "
                "SubscriptionManager before calling subscribe()"
            )
        return self.subscriptions.subscribe(strip_name, visibility)

    def publish(
        self,
        strip_name: str,
        event: Any,
        *,
        phase: DerivationPhase = DerivationPhase.CANONICAL,
        time_range: Optional[TimeRange] = None,
    ) -> int:
        """Publish to a named strip.

        No-op (returns 0) when no `SubscriptionManager` is bound — nodes
        that publish should not need to check for the manager's presence;
        the call simply fans out to zero subscribers when nobody is
        listening.
        """
        if self.subscriptions is None:
            return 0
        return self.subscriptions.publish(
            strip_name, event, phase=phase, time_range=time_range
        )


__all__ = ["ChunkedContext"]
