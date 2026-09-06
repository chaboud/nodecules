# Temporal Scheduling in nodecules

**Status**: design document for the `feat/temporality` branch. Do not merge to `main` until the existing static-DAG test suite is green and the new temporal path has its own test suite.

## Motivation

Nodecules today executes a static DAG once, topologically sorted. Several downstream consumers (stenota being the first) need to run *the same DAG across a sliding time axis*, with:

- cross-window data flow (later-window nodes consuming earlier-window outputs)
- content-addressable caching keyed in part by the time window
- cheap re-runs when upstream data changes (annotations, late-arriving evidence)
- three execution modes over the same topology: batch, live, interactive-review

This document specifies the minimal additions to nodecules that support these requirements without disturbing the existing static-DAG execution path.

## Design constraints

1. **Additive.** Existing nodes, graphs, and executions must continue to work unchanged. `temporal_kind=static` is the default.
2. **Time is data, not control.** A `TimeSource` is an injected dependency of the scheduler, not a global. Batch and live differ only in which `TimeSource` is wired in.
3. **Cache keys are explicit.** The cache layer already exists (content-addressable contexts). We extend the key schema, not the mechanism.
4. **No new graph format.** Temporal info lives in `NodeSpec` annotations and scheduler config, not in a new JSON schema.

## New primitives

### `TimeRange`

A new `DataType.TIME_RANGE` and a Pydantic model:

```python
class TimeRange(BaseModel):
    start_ms: int       # meeting-relative, non-negative
    end_ms: int         # > start_ms

    def intersects(self, other: "TimeRange") -> bool: ...
    def contains(self, ms: int) -> bool: ...
    def union(self, other: "TimeRange") -> "TimeRange": ...
    def intersection(self, other: "TimeRange") -> "TimeRange | None": ...
    def shift(self, delta_ms: int) -> "TimeRange": ...
    def duration_ms(self) -> int: ...
```

Times are integer milliseconds. No floats. No timezone-aware datetimes. Meeting-relative (t=0 at first sample of first input). Epoch timestamps are converted at ingest and at render, never mid-pipeline.

**Generalised 2026-09-06, additively** (`core/timeline.py`, founder input:
the domain and provenance of time are metadata; devices and media skew;
milliseconds are not enough for every content type). A time is an integer
count of ticks on a *named timeline* whose timebase is an exact rational —
milliseconds are 1/1000, 100 ns "nanos" are 1/10,000,000, a 48 kHz sample
clock is 1/48000, 29.97 fps is 1001/30000. The meeting timeline above is
the 1/1000 case and every `_ms` field keeps meaning exactly what it did.
Two timelines relate only through a `TimelineMap` of measured anchors with
its own provenance and error bound; conversion is exact rational
arithmetic that reports whether rounding was needed. Still no floats,
still no epoch mid-pipeline: an epoch is just a timeline whose origin is
`unix-epoch`, and you convert through a map, on purpose, where you can see
it.

### `TimeSource`

```python
class TimeSource(Protocol):
    async def now_ms(self) -> int: ...
    async def wait_until(self, ms: int) -> None: ...
    def is_closed(self) -> bool: ...
```

Three implementations in nodecules core:

- `WallClock` — live mode. `now_ms()` returns monotonic ms since start; `wait_until` sleeps.
- `FileClock` — batch mode. Advanced by the demuxer or caller. `wait_until` is a no-op (the scheduler just advances the clock when upstream windows close).
- `ManualClock` — review mode. Controlled by UI commands. `wait_until` blocks on a signal.

### `ChunkedContext`

Extends `ExecutionContext`:

```python
class ChunkedContext(ExecutionContext):
    current_window: TimeRange
    time_source: TimeSource

    def get_inputs_in_window(self, node_id: str, port: str, window: TimeRange) -> list[Any]: ...
    def get_annotations_in_window(self, window: TimeRange) -> list[AnnotationRef]: ...
    def annotation_hash_for_window(self, window: TimeRange) -> str: ...
```

Static nodes ignore the new fields. Temporal nodes read them.

### `NodeSpec` additions

```python
class NodeSpec(BaseModel):
    # existing fields unchanged...
    temporal_kind: Literal["static", "windowed", "streaming", "reanneal"] = "static"
    window_spec: WindowSpec | None = None
    emit_policy: Literal["streaming", "on_window_close", "on_graph_close"] = "on_window_close"
    supports_reanneal: bool = False
```

```python
class WindowSpec(BaseModel):
    size_ms: int
    stride_ms: int              # < size_ms for overlap
    align: Literal["origin", "boundary"] = "origin"
    min_upstream_coverage: float = 1.0   # fraction of window that must have upstream data before run
```

`temporal_kind` semantics:

- **static** — runs once per graph execution. Default. Existing behavior.
- **windowed** — runs once per window as defined by `window_spec`. Inputs are sliced to the window. Output carries the window as a `TimeRange`.
- **streaming** — runs continuously as inputs arrive; may emit multiple times before its "window" closes. Used for ASR-like token-streaming nodes.
- **reanneal** — runs only on explicit request (annotation landed, user-triggered re-anneal, graph close). Not on windowed cadence.

### Output-level mutability

Every output artifact from a temporal node carries a `mutability` field: `wet | drying | dry | smudged`. Nodecules does not enforce semantics on this field; it's defined by the consumer (stenota's re-anneal horizon rules). Nodecules guarantees the field round-trips through the cache.

## The scheduler

`TemporalScheduler` wraps the existing Kahn's-algorithm executor:

```
loop:
    t = await time_source.now_ms()
    ready = compute_ready_set(graph, t)     # windowed nodes whose window closed at or before t
                                             # + reanneal nodes with smudged cache entries
                                             # + streaming nodes with queued inputs
    for (node, window) in ready:
        if cache.has(cache_key(node, window, inputs, annotations)):
            emit from cache
        else:
            result = await existing_executor.run_single(node, ChunkedContext(window=window, ...))
            cache.put(key, result)
            emit result
    if time_source.is_closed() and ready == empty:
        run on_graph_close nodes
        break
    await time_source.wait_until(next_tick_ms)
```

`compute_ready_set` is the only substantive new logic. It walks the graph per-node and asks:

- static: has it run yet in this graph execution? If no, and upstream static deps are satisfied, it's ready.
- windowed: is there a window `W` such that upstream data coverage over `W` ≥ `min_upstream_coverage` AND `(node, W)` has no cache entry OR has a smudged entry?
- streaming: are there unconsumed upstream emissions?
- reanneal: is there a smudged cache entry and (annotation arrived OR explicit request OR graph close)?

The existing executor runs one node at a time; the scheduler is the outer loop deciding which `(node, window)` to hand to it next. Parallelism across windows is a v2 concern — get correctness first.

## Cache keys

Extend the existing content-addressable key to:

```
cache_key = hash(
    node_type,
    node_version,
    params_hash,
    input_hashes,              # already present
    window_hash,               # new: hash of TimeRange; null for static nodes
    annotation_hash,           # new: hash of annotations intersecting window; null if node doesn't read annotations
)
```

Existing cache entries remain valid (their `window_hash` and `annotation_hash` are null and match static nodes). New temporal nodes produce new keys. No migration needed.

## Annotations

Nodecules defines an `AnnotationNode` base type (stenota subclasses it). It has:

- `node_id`, `emission_ms`, `target_window: TimeRange`, `payload: dict`
- a content hash derived from all of the above

The scheduler maintains an index: `window → list[AnnotationRef]`, updated when annotation nodes emit or retract. `ChunkedContext.get_annotations_in_window` reads from this index. The annotation hash is a canonical hash of the sorted list of annotation content-hashes intersecting the window.

Adding an annotation whose `target_window` intersects `W` invalidates cache entries keyed with a different `annotation_hash` for any node that reads annotations in `W`. Nodes that don't read annotations are unaffected regardless of annotation churn (their cache key doesn't include `annotation_hash`).

## Emit policies

- **streaming** — scheduler emits the node's outputs into downstream queues as they are produced. Suitable for ASR streaming tokens.
- **on_window_close** — scheduler emits after `run_single` completes for a window. The common case.
- **on_graph_close** — scheduler holds the node back until `time_source.is_closed()`. Whole-meeting summarizers use this.

A single node uses one emit policy. Multiple resolutions = multiple nodes (this is why stenota has separate L3a and L3b summarizer nodes).

## Tests to land with the branch

Before merge to `main`:

1. Full existing static-DAG test suite passes unchanged.
2. `TimeRange` unit tests (intersect/union/shift/contains edge cases).
3. `FileClock` + `TemporalScheduler` integration test: a small windowed graph over a synthetic input sequence, asserting window coverage and cache hits on re-run.
4. Annotation invalidation test: run a windowed graph, add an annotation in one window, assert only that window's cache entry is invalidated and re-run.
5. Emit policy test: assert `on_graph_close` nodes do not fire until the `TimeSource` reports closed.
6. Provider-adapter contract tests for temporal nodes mirror the existing static tests.

## Out of scope for this branch

- Parallel execution of independent `(node, window)` pairs. Single-threaded for now.
- Cross-graph scheduling (running multiple meetings concurrently sharing model resources). This is a v2 problem.
- Distributed scheduling. Not happening.
- Backpressure from slow consumers to fast producers. Naive queuing for now; explicit backpressure later if it becomes a real problem.
