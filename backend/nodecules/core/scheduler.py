"""`TemporalScheduler` — wraps `GraphExecutor` with window + cache + annotations.

Scope of this PR (PR-n3):

- **Batch mode** — `run_batch(total_duration_ms, ...)`. A known-finite input
  stream; the scheduler enumerates every window in `[0, total_duration_ms)`
  for each windowed node, executes them in temporal+topological order, and
  fires `on_graph_close` nodes last. Driven by `FileClock`.
- **Static nodes** — run once, upstream-first, exactly as the existing
  `GraphExecutor` does today (we delegate per-node execution to its
  `_execute_node`).
- **Windowed nodes** — run once per window. Cache-aware: hits skip the
  call; misses execute and store. Each window's result lives on the
  `ChunkedContext` under `(node_id, window_digest)` so downstream
  windowed nodes at the same window can pull it.
- **`on_graph_close` nodes** — held back until every upstream is done.
  These are the full-meeting summarizers / reducers.
- **Annotation participation** — if a node declares `supports_reanneal`
  AND an `AnnotationIndex` is bound to the context, the cache key for
  its `(node, window)` includes the current annotation hash. Adding or
  removing annotations automatically smudges only the intersecting
  windows.

Out of scope (documented, not built):

- **Streaming nodes** — `temporal_kind="streaming"` raises at construction.
- **Live / wall-clock mode** — the `WallClock` / `ManualClock`-driven
  loop will land in a later PR. Scope discipline: batch first.
- **JSONL-backed lazy input streaming** — windowed nodes here pull the
  full upstream output from `ExecutionContext.node_outputs` and window
  it themselves. Long-meeting sidecar-streamed lookups land when the
  stenota side integrates.
- **Parallel window execution** — single-threaded. Correctness first.
- **Backpressure** — naive queuing; explicit backpressure later if it
  becomes a real problem.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass, field
from datetime import datetime
from typing import Any, Dict, Iterator, List, Optional, Tuple, Type

import dataclasses
import time as _time

from .annotations import AnnotationIndex, content_hashes_for_window
from .events import EventSink, ExecutionEvent, NullEventSink
from .executor import ExecutionError, GraphExecutor
from .graph import GraphExecutionPlanner
from .node_cache import (
    CacheKey,
    FilesystemNodeCache,
    InMemoryNodeCache,
    NodeCache,
    build_cache_key,
)
from .temporal_context import ChunkedContext
from .time import FileClock, TimeRange, TimeSource
from .types import (
    BaseNode,
    GraphData,
    NodeData,
    NodeStatus,
    WindowSpec,
)


logger = logging.getLogger(__name__)


# --- Window enumeration --------------------------------------------------


def compute_windows(
    spec: WindowSpec,
    total_range: TimeRange,
) -> List[TimeRange]:
    """Enumerate the windows a `spec` covers over `total_range`.

    Overlapping windows (`stride < size`) are supported. A window is
    included if it has any overlap with `total_range` — we trust
    `min_upstream_coverage` to be enforced by the scheduler at dispatch
    time, not here. The last window may extend past `total_range.end_ms`.
    Callers that want strict clamping should post-filter.
    """
    starts: List[int] = []
    if spec.align == "origin":
        # Anchor at 0. First window starts at the largest k*stride not
        # past `total_range.start_ms`.
        k0 = max(0, total_range.start_ms // spec.stride_ms)
        start = k0 * spec.stride_ms
    else:
        # "boundary" — align to total_range's start.
        start = total_range.start_ms

    while start < total_range.end_ms:
        starts.append(start)
        start += spec.stride_ms

    return [TimeRange(start_ms=s, end_ms=s + spec.size_ms) for s in starts]


# --- Scheduler state -----------------------------------------------------


@dataclass
class _SchedulerContext:
    """Carries the per-run bookkeeping the scheduler needs on top of
    `ChunkedContext`. Kept separate from the public context to avoid
    bleeding scheduler internals into node-visible surface area."""
    # Windowed outputs indexed by (node_id, window_digest). Each value is
    # the dict of port→value the node returned. Lives here rather than on
    # `ChunkedContext` so the node-facing surface stays minimal.
    windowed_outputs: Dict[Tuple[str, str], Dict[str, Any]] = field(default_factory=dict)
    # (node_id, window_digest) pairs that have completed in this run.
    completed_windowed: set = field(default_factory=set)


# --- Scheduler -----------------------------------------------------------


class TemporalScheduler:
    """Batch-mode temporal scheduler.

    Wraps an existing `GraphExecutor` — the `BaseNode.execute()` contract
    is unchanged. A windowed node reads `context.current_window` from
    its `ChunkedContext` and filters its own upstream view; the
    scheduler's responsibility is cadence + cache + annotation
    participation, not data slicing.
    """

    def __init__(
        self,
        node_registry: Dict[str, Type[BaseNode]],
        *,
        time_source: Optional[TimeSource] = None,
        cache: Optional[NodeCache] = None,
        annotation_index: Optional[AnnotationIndex] = None,
        event_sink: Optional[EventSink] = None,
    ) -> None:
        self._node_registry = node_registry
        self._executor = GraphExecutor(node_registry)
        # `is None` guards throughout — NOT `or` — because containers
        # with `__len__ == 0` are falsy in Python. An empty
        # `InMemoryNodeCache` or `ListEventSink` passed by the caller
        # would otherwise be silently replaced by a fresh default.
        self._time_source: TimeSource = (
            time_source if time_source is not None else FileClock()
        )
        self._cache: NodeCache = (
            cache if cache is not None else InMemoryNodeCache()
        )
        self._annotation_index = annotation_index
        self._events: EventSink = (
            event_sink if event_sink is not None else NullEventSink()
        )

        # Early check — we don't support streaming in this PR. Fail at
        # construction so callers know before a long batch.
        for node_id, node_data in []:
            pass  # graph is supplied per-run, not per-scheduler

    # -- Public entry points ---------------------------------------------

    async def run_batch(
        self,
        graph: GraphData,
        *,
        total_duration_ms: int,
        initial_inputs: Optional[Dict[str, Any]] = None,
    ) -> ChunkedContext:
        """Run `graph` over a known-finite time range.

        Args:
            graph: the DAG to run.
            total_duration_ms: the [0, total_duration_ms) time range
                windowed nodes enumerate over. Usually comes from the
                demuxer (stenota) or a test fixture.
            initial_inputs: keyed as in `ExecutionContext.execution_inputs`.

        Returns a `ChunkedContext` with static outputs on
        `.node_outputs` and windowed outputs accessible via
        `get_windowed_output()`.
        """
        self._reject_streaming(graph)

        total_range = TimeRange(start_ms=0, end_ms=total_duration_ms)

        context = ChunkedContext(
            execution_id="",
            graph=graph,
            execution_inputs=initial_inputs or {},
            started_at=datetime.utcnow(),
            time_source=self._time_source,
            annotation_index=self._annotation_index,
        )
        sched = _SchedulerContext()

        for node_id in graph.nodes:
            context.set_node_status(node_id, NodeStatus.PENDING)

        self._emit(
            ExecutionEvent(
                kind="graph_start",
                graph_id=graph.graph_id,
                execution_id=context.execution_id,
                meta={"total_duration_ms": total_duration_ms},
            )
        )

        planner = GraphExecutionPlanner(graph)
        order = planner.get_execution_order()

        # Drive the FileClock forward if that's what we have — lets nodes
        # that care about `now_ms()` see a monotonically-advancing clock.
        file_clock = self._time_source if isinstance(self._time_source, FileClock) else None

        for node_id in order:
            node_data = graph.nodes[node_id]
            spec = self._get_spec(node_data)
            kind = getattr(spec, "temporal_kind", "static")
            emit = getattr(spec, "emit_policy", "on_window_close")

            if kind == "static":
                if emit == "on_graph_close":
                    # Static + on_graph_close: defer until the end.
                    continue
                await self._run_static_with_events(context, node_id, spec)
            elif kind == "windowed":
                await self._run_windowed_node(
                    context, sched, node_id, total_range
                )
                if file_clock is not None:
                    file_clock.advance_to(total_range.end_ms)
            elif kind == "reanneal":
                # Re-anneal nodes only run on request or at graph close.
                # Deferred below.
                continue
            else:
                raise NotImplementedError(
                    f"temporal_kind={kind!r} not supported in PR-n3 scheduler"
                )

        # Mark stream closed so `is_closed()` is True for on_graph_close nodes.
        if file_clock is not None:
            file_clock.close()

        # Graph-close pass: anything with emit_policy=on_graph_close, plus
        # any reanneal nodes, fires now in topological order.
        for node_id in order:
            node_data = graph.nodes[node_id]
            spec = self._get_spec(node_data)
            emit = getattr(spec, "emit_policy", "on_window_close")
            kind = getattr(spec, "temporal_kind", "static")
            if emit == "on_graph_close" or kind == "reanneal":
                # Provide the total range on `current_window` so nodes
                # that want to know what span they're reducing over can
                # read it.
                context.current_window = total_range
                await self._run_static_with_events(context, node_id, spec)
                context.current_window = None

        context.completed_at = datetime.utcnow()
        self._emit(
            ExecutionEvent(
                kind="graph_close",
                graph_id=graph.graph_id,
                execution_id=context.execution_id,
            )
        )
        self._events.close()
        return context

    def get_windowed_output(
        self, context: ChunkedContext, node_id: str, window: TimeRange
    ) -> Optional[Dict[str, Any]]:
        """Return the per-port output dict for a `(node, window)` pair."""
        digest = self._window_digest(window)
        return self._sched_for(context).windowed_outputs.get((node_id, digest))

    # -- Scheduler-local helpers -----------------------------------------

    async def _run_windowed_node(
        self,
        context: ChunkedContext,
        sched: _SchedulerContext,
        node_id: str,
        total_range: TimeRange,
    ) -> None:
        node_data = context.graph.nodes[node_id]
        spec = self._get_spec(node_data)
        if spec.window_spec is None:
            raise ExecutionError(
                f"node {node_id} temporal_kind=windowed but no window_spec"
            )
        windows = compute_windows(spec.window_spec, total_range)
        if not windows:
            logger.info("no windows to run for %s", node_id)
            return

        # Bind scheduler-local bookkeeping on the context for the
        # integration test and get_windowed_output() to see.
        self._attach_sched(context, sched)

        for window in windows:
            await self._execute_window(context, sched, node_data, spec, window)

        # Consolidate per-window outputs into `context.node_outputs` so
        # downstream nodes can read them through the normal input-port
        # API. Per port, the consolidated value is a list of the
        # per-window port values in window order. Nodes that need
        # per-window detail can look up via `get_windowed_output()`.
        by_port: Dict[str, List[Any]] = {}
        for window in windows:
            digest = self._window_digest(window)
            win_out = sched.windowed_outputs.get((node_id, digest), {})
            for port in spec.outputs:
                by_port.setdefault(port.name, []).append(win_out.get(port.name))
        for port_name, values in by_port.items():
            context.set_node_output(node_id, port_name, values)
        context.set_node_status(node_id, NodeStatus.COMPLETED)

    async def _execute_window(
        self,
        context: ChunkedContext,
        sched: _SchedulerContext,
        node_data: NodeData,
        spec,  # NodeSpec
        window: TimeRange,
    ) -> None:
        node_id = node_data.node_id

        # Build the cache key — includes annotation hash when the node
        # opts in via supports_reanneal (treating reanneal as
        # "participates in annotation cache invalidation"). Static
        # cache-key semantics stay: no window, no annotation hash.
        inputs = self._collect_inputs_for_node(context, node_id)
        if getattr(spec, "supports_reanneal", False) and context.annotation_index is not None:
            ann_hashes = content_hashes_for_window(context.annotation_index, window)
        else:
            ann_hashes = None

        key = build_cache_key(
            node_type=spec.node_type,
            node_version=self._node_version(node_data),
            params=node_data.parameters,
            inputs=inputs,
            window=window,
            annotation_content_hashes=ann_hashes,
        )

        # Windowed bookkeeping is indexed by (node_id, window_hash) —
        # independent of cache-key digest so the consolidation step can
        # look up by window alone.
        window_digest = self._window_digest(window)
        if self._cache.has(key):
            cached = self._cache.get(key)
            sched.windowed_outputs[(node_id, window_digest)] = (
                cached if isinstance(cached, dict) else {"_cached": cached}
            )
            sched.completed_windowed.add((node_id, window_digest))
            self._emit_node(
                "cache_hit",
                context=context,
                node_data=node_data,
                window=window,
                cache_key_digest=key.digest(),
            )
            logger.debug("cache hit %s @ %s", node_id, window)
            return

        cache_key_digest = key.digest()
        self._emit_node(
            "node_start",
            context=context,
            node_data=node_data,
            window=window,
            cache_key_digest=cache_key_digest,
        )

        # Cache miss — execute against a per-window context view.
        prev_window = context.current_window
        context.current_window = window
        started_wall = _time.monotonic()
        try:
            if node_data.node_type not in self._node_registry:
                raise ExecutionError(f"Unknown node type: {node_data.node_type}")
            node_class = self._node_registry[node_data.node_type]
            node_instance = node_class()

            # Collect inputs. The existing executor's `_execute_node`
            # writes to `context.node_outputs`; for windowed nodes we
            # don't want their output to overwrite a previous window's,
            # so we copy the per-node outputs aside, run, capture, and
            # restore. This isolates windows without changing the
            # BaseNode.execute() contract.
            prior_outputs = context.node_outputs.get(node_id)

            outputs = await node_instance.execute(context, node_data)

            sched.windowed_outputs[(node_id, window_digest)] = outputs
            sched.completed_windowed.add((node_id, window_digest))

            # Restore prior outputs (if any) so downstream static nodes
            # that expected static access see nothing new from this node.
            if prior_outputs is not None:
                context.node_outputs[node_id] = prior_outputs
            else:
                context.node_outputs.pop(node_id, None)

            self._cache.put(key, outputs)
            latency_ms = int((_time.monotonic() - started_wall) * 1000)
            self._emit_node(
                "node_complete",
                context=context,
                node_data=node_data,
                window=window,
                cache_key_digest=cache_key_digest,
                latency_ms=latency_ms,
            )
            self._emit_node(
                "window_emit",
                context=context,
                node_data=node_data,
                window=window,
                cache_key_digest=cache_key_digest,
            )
            logger.debug("ran %s @ %s", node_id, window)
        except Exception as exc:
            context.set_node_status(node_id, NodeStatus.FAILED)
            context.errors[node_id] = str(exc)
            self._emit_node(
                "node_failed",
                context=context,
                node_data=node_data,
                window=window,
                cache_key_digest=cache_key_digest,
                error=str(exc),
            )
            raise ExecutionError(f"Node {node_id} failed at window {window}: {exc}") from exc
        finally:
            context.current_window = prev_window

    async def _run_static_with_events(
        self,
        context: ChunkedContext,
        node_id: str,
        spec,
    ) -> None:
        """Delegate to `GraphExecutor._execute_node` but book-end with events.

        Static nodes don't participate in the windowed cache by default
        in PR-n3; the event log still captures their execution so the
        timeline view covers the whole graph.
        """
        node_data = context.graph.nodes[node_id]
        self._emit_node(
            "node_start",
            context=context,
            node_data=node_data,
            window=context.current_window,
        )
        started = _time.monotonic()
        try:
            await self._executor._execute_node(context, node_id)
        except Exception as exc:
            self._emit_node(
                "node_failed",
                context=context,
                node_data=node_data,
                window=context.current_window,
                error=str(exc),
            )
            raise
        latency_ms = int((_time.monotonic() - started) * 1000)
        self._emit_node(
            "node_complete",
            context=context,
            node_data=node_data,
            window=context.current_window,
            latency_ms=latency_ms,
        )

    # --- Event helpers --------------------------------------------------

    def _emit(self, event: ExecutionEvent) -> None:
        self._events.emit(event)

    def _emit_node(
        self,
        kind,
        *,
        context: ChunkedContext,
        node_data: NodeData,
        window: Optional[TimeRange] = None,
        cache_key_digest: Optional[str] = None,
        latency_ms: Optional[int] = None,
        error: Optional[str] = None,
    ) -> None:
        """Emit a node-scoped event, auto-filling common fields."""
        meeting_ts: Optional[int]
        if window is not None:
            meeting_ts = window.start_ms
        else:
            meeting_ts = None

        self._emit(
            ExecutionEvent(
                kind=kind,
                graph_id=context.graph.graph_id,
                execution_id=context.execution_id,
                meeting_ts_ms=meeting_ts,
                node_id=node_data.node_id,
                node_type=node_data.node_type,
                node_version=self._node_version(node_data),
                window=window,
                cache_key_digest=cache_key_digest,
                latency_ms=latency_ms,
                error=error,
            )
        )

    def _collect_inputs_for_node(
        self, context: ChunkedContext, node_id: str
    ) -> Iterator[Any]:
        """Read the upstream-port values that feed `node_id`, in port order.

        Used only for cache-key input hashing. A `None` value (port not
        wired) contributes the `None` hash — that's intentional; adding
        an edge must invalidate the key.
        """
        node_data = context.graph.nodes[node_id]
        spec = self._get_spec(node_data)
        values: List[Any] = []
        for port in spec.inputs:
            values.append(context.get_input_value(node_id, port.name))
        return values

    @staticmethod
    def _window_digest(window: TimeRange) -> str:
        from .node_cache import hash_window

        h = hash_window(window)
        # hash_window returns None only for window=None, which isn't
        # possible here. Asserting quiets mypy without runtime cost.
        assert h is not None
        return h

    def _reject_streaming(self, graph: GraphData) -> None:
        """Early-fail when a graph declares `temporal_kind="streaming"`.

        Keeps PR-n3 scope honest: the streaming emit path is a later PR.
        """
        for node_id, node_data in graph.nodes.items():
            spec = self._get_spec(node_data)
            if getattr(spec, "temporal_kind", "static") == "streaming":
                raise NotImplementedError(
                    f"node {node_id} has temporal_kind='streaming'; "
                    "streaming is not supported in PR-n3"
                )

    def _get_spec(self, node_data: NodeData):
        """Instantiate a node to read its spec, then apply per-instance
        parameter overrides.

        Node types are *primitives* (think shader-graph nodes) that get
        instantiated with configuration. A generic `llm.tool_loop`
        primitive, for example, may be used as a static on_graph_close
        lens in one place and a windowed summarizer in another, differing
        only in `NodeData.parameters`. Applying overrides here lets the
        scheduler enumerate windows / emit policies correctly for each
        instance without requiring a distinct Python class per use.

        Overridable fields: `temporal_kind`, `emit_policy`,
        `supports_reanneal`, `window_spec`.
        """
        cls = self._node_registry.get(node_data.node_type)
        if cls is None:
            raise ExecutionError(f"Unknown node type: {node_data.node_type}")
        spec = cls().spec
        return _apply_param_overrides(spec, node_data.parameters or {})

    def _node_version(self, node_data: NodeData) -> str:
        """Node version string used in cache keys. Defaults to "0.1.0"
        when a node doesn't advertise one; real nodes should stamp this
        explicitly."""
        params = node_data.parameters or {}
        return str(params.get("node_version", "0.1.0"))

    # Scheduler-to-context binding. Use private attribute name so
    # ChunkedContext stays a clean dataclass without a scheduler handle
    # in its public shape.
    _SCHED_ATTR = "_nodecules_sched"

    def _attach_sched(self, context: ChunkedContext, sched: _SchedulerContext) -> None:
        setattr(context, self._SCHED_ATTR, sched)

    def _sched_for(self, context: ChunkedContext) -> _SchedulerContext:
        got = getattr(context, self._SCHED_ATTR, None)
        if got is None:
            raise RuntimeError(
                "scheduler context not bound; call run_batch() before get_windowed_output()"
            )
        return got


def _apply_param_overrides(spec, params: Dict[str, Any]):
    """Return a `NodeSpec` with instance-level overrides applied.

    Leaves `spec` untouched if `params` contains no override keys —
    keeps pre-existing nodes unaffected.
    """
    overrides: Dict[str, Any] = {}
    if "temporal_kind" in params:
        overrides["temporal_kind"] = params["temporal_kind"]
    if "emit_policy" in params:
        overrides["emit_policy"] = params["emit_policy"]
    if "supports_reanneal" in params:
        overrides["supports_reanneal"] = bool(params["supports_reanneal"])
    if "window_spec" in params and params["window_spec"] is not None:
        ws = params["window_spec"]
        if isinstance(ws, WindowSpec):
            overrides["window_spec"] = ws
        elif isinstance(ws, dict):
            overrides["window_spec"] = WindowSpec(**ws)
    if not overrides:
        return spec
    return dataclasses.replace(spec, **overrides)


__all__ = ["TemporalScheduler", "compute_windows"]
