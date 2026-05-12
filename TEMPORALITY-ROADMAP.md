# Temporality Roadmap

This document captures the architectural arc that begins with `feat/temporality` and extends through the substrate, op-graph versioning, slots, strips, scheduler v2, environment, providers, and subscriptions. It is a roadmap, not a design spec — each phase will land with its own design doc as it's built.

`TEMPORALITY.md` remains the design of the primitives already on this branch. This roadmap describes what comes *after* and how those primitives fit into the larger model.

## Where we are

`feat/temporality` ships:

- `core/time.py` — `TimeRange`, `TimeSource`, `WallClock`, `FileClock`, `ManualClock`
- `core/temporal_context.py` — `ChunkedContext`
- `core/annotations.py` — `AnnotationNode`, `AnnotationRef`, `AnnotationIndex`
- `core/node_cache.py` — `CacheKey`, `InMemoryNodeCache`, `FilesystemNodeCache`
- `core/events.py` — `ExecutionEvent` log + sinks
- `core/scheduler.py` — `TemporalScheduler.run_batch` (batch-startup window enumeration)
- `NodeSpec` additions: `temporal_kind`, `window_spec`, `emit_policy`, `supports_reanneal`

The primitives are correct. The scheduler is an MVP — a batch-startup enumeration loop, not the long-term event-driven shape. Stenota v0.1 runs against it as the integration canary.

## Where we're going

Eight phases, all additive, static-DAG execution path preserved throughout.

### Phase 1 — Substrate

An append-only event log with a persistent root: HAMT-style maps + persistent vector tries in memory, JSONL-on-disk for durability, atomic CAS on the root pointer for publication. Readers acquire a root and see a consistent snapshot; writers append. Wait-free reads.

Two durability classes:

- **Raw evidence events** (frames, embeddings, ASR segments, annotations, sensor readings) — pinned permanently. Cannot be regenerated.
- **Derivation events** (clusters, claims, summaries, lens outputs) — *cache*. Re-derivable from `(op_graph_version, input_event_hashes, params)`. Aggressively evictable.

Existing `node_cache.py` becomes the derivation projection. Existing `events.py` becomes the substrate log shape. `ChunkedContext.get_input_value` semantics get fixed (PR-n3 has known footguns with windowed→windowed within-window reads).

### Phase 2 — Op-graphs + version sets as substrate events

Op-graphs become content-hashed substrate artifacts with `parent_id` lineage — git-shaped. Tags are movable named refs (e.g., `voiceProcessing/diarizationLive`) maintained under CAS.

Version sets are content-hashed manifests of `{slot_name → processor_hash}`. Three resolution policies, npm-shaped: pinned (every slot explicit), loose (tag refs frozen at instantiation into a `.lock`), mixed.

Instantiation = `(op_graph_hash, version_set_hash)`. That pair fully determines what code runs. Both content hashes go into derivation lineage.

Self-modification (a robot publishing a new op-graph, an MCP `add_lens` tool) becomes "append a new event," no mutation, no migration.

### Phase 3 — Slots + contracts

`node_type` registry becomes `(slot_name, slot_version) → [processor]`. Each slot is a `(name, contract)` pair; the contract is typed I/O ports + parameter schema. Multiple processors can target the same slot. Slot contracts are themselves versioned — `voiceProcessing/diarizationLive@v2` differs from `@v1` and processors don't auto-upgrade.

A processor satisfying a slot can be a leaf node or a subgraph. The caller doesn't care which.

Validation: a processor's `NodeSpec` must satisfy its declared slot's contract. Version sets that don't bind every required slot can't instantiate.

Backward compat: existing `NODE_TYPE = "smart_chat"` declarations transparently become `SLOT = "smart_chat@v1"` declarations with auto-generated contracts.

### Phase 4 — Strips

A strip is a *named source slot* — a named, addressable, time-extended publication in the substrate. Consumers depend on strips by name, not by node ID. The producer can be swapped (via the version set) without touching consumers.

Stenota's L0/L1/L2/L3a/L3b/L4 hierarchy is exactly a strip tree:

- `strips/audio/raw` (L0)
- `strips/segments/vad` (L1, IIR-depends on raw)
- `strips/turns/diarized` (L2, IIR-depends on segments + embeddings)
- `strips/claims/L3a@5min` (L3a, IIR-depends on turns)
- `strips/claims/L4.summary` (L4, on-graph-close)

Each strip declares: **schema/contract** (versioned), **producer subgraph**, **settling requirement**, **history reach**.

Indexing API — NumPy-shaped, both absolute and self-relative:

```python
prev_note = notes[me - 1]
last_five = notes[me - 5 : me]
recent_turns = turns.in_range(start, end)
prior_section = sections.before(my_time_range)
```

Forward indexing within your own strip (`me + k`, k ≥ 0) is a graph-validation reject — the cycle guard. Cross-strip forward reads forbidden by temporal monotonicity; type system can enforce when strips declare relative depth.

Strips are most cheaply implemented as a **naming convention over derivation events**: each strip "exists" as an index over derivations matching `(name, schema_version)`. No parallel data structure.

### Phase 5 — Scheduler v2

`run_batch` (renamed to `run_batch_oneshot` here) is replaced by an event-driven cooking loop. Dirty queue is first-class; tags name *why* something needs cooking (`stale-data`, `stale-graph`, `stale-annotation`, `pending-reanneal`, `served`, `blocking`). Hare consumes `{blocking, stale-data}`; tortoise consumes the rest under budget. External events (annotations landing, graph edits) write tags. The scheduler is a dispatcher.

**Three modes, one engine, three configurations:**

- Single-shot: no windows, no queue. The static-DAG path.
- Continuous low-latency: event-driven queue, hare-only, served pointer updates eagerly.
- Offline high-latency: event-driven queue, both workers, initial state is a flood of "everything stale" entries the tortoise chews through.

Batch becomes a degenerate case of live.

**IIR/feedback support:**

- `previous(port, lag=k)` primitive distinguishes one-window-lagged feedback from algebraic loops
- `settling_windows` declared on nodes/subgraphs; cooker compounds through subgraphs
- Pre-roll: cooker reaches back into substrate history to prime IIR state. Zero-fill is the universal initial condition; history-reach is an optimization that reduces the warmup transient.
- `phase ∈ {warmup, canonical, superseded}` on emitted derivations. "Filter just reset" flag, not "missing data" flag. Generalizes across cold-start, processor-swap, op-graph-swap.
- Cold-start vs resume signal to nodes: lets algorithms with efficient empty-state initialization skip the full zero-fill convergence.

### Phase 6 — Environment

Disentangle `ExecutionContext` into three things:

- **`Environment`** — read-only ambient capabilities (substrate root, providers, clock, annotation index). Declared on `NodeSpec` as `reads_env=["substrate", "llm.default", "time"]`.
- **Append-only sinks** — substrate writer, signal emitter, log sink. Declared as `writes_env=["substrate", "signals.attention"]`.
- **Per-execution context** — outputs being accumulated, errors, status. Stays as the writable per-execution thing.

Scoped overrides: a subgraph can override `llm.default` for everything inside it without affecting the parent. Per-graph, per-subgraph, per-node granularity.

The runtime refuses to instantiate a node whose env deps aren't satisfied — same as static type checking on a function call. Failures move from runtime crashes to instantiation-time errors.

### Phase 7 — Tool-aware provider adapter

Current `core/smart_context.py` is chat-shaped: `generate_with_context(context_data, new_message, **kwargs) → (str, dict)`. No tool use, no JSON-schema-constrained output. Stenota's `stenota_graph/agent.py` calls Ollama directly because the abstraction doesn't fit its tool-loop pattern.

Phase 7 extends the adapter contract: add a `generate_with_tools(messages, tools, schema=None, **kwargs) → (response, tool_calls, updated_context)` shape. Existing chat call paths keep working. The four current adapters (Ollama, Anthropic, Bedrock, Mock) gain tool support. Stenota's `LensAgent` direct-call moves inside the abstraction.

Result: `LLMToolLoopNode` works against Ollama, Claude, and Bedrock via the same node code with only a provider param change.

### Phase 8 — Strip subscriptions (push API)

Not a parallel effect/signal channel. The strip IS the signal source. Strip indexing is *pull*; strip subscription is *push*. Both backed by the same substrate events; both monotone-temporal by default.

A subscriber says "wake me when matching events land in `strips/X`." Default visibility: canonical-phase events at time ≤ subscriber's current `now`. Explicit configuration changes scope:

```python
sub = substrate.subscribe(
    "strips/claims/salient.interjections",
    visibility=Visibility(
        phases={Phase.CANONICAL, Phase.WARMUP},
        time_horizon=lambda now: TimeRange(now - 30*MIN, now),
    ),
)
```

Stenota's attention-request mechanic falls out: L2 summarizer publishes to `strips/attention.requests`, VLM sampler subscribes, fires when a request event lands. Robot interjection: subscribe to `strips/claims/salient.interjections`, react on fire. Internal scheduler readiness uses the same mechanism: a windowed node "is ready" iff its upstream strip subscriptions have fired with the inputs it needs.

## Hard invariants across all phases

1. **Static-DAG execution path does not regress.** Every existing node, every existing example graph, every existing test keeps working before and after every phase.
2. **All changes additive.** New fields on `NodeSpec` have defaults. Reordering existing dataclass fields is forbidden.
3. **Time is integer milliseconds, meeting-relative.** No floats. No Unix epoch mid-pipeline.
4. **`TimeSource` injected, never global.** Never call `time.time()` or `datetime.now()` in the scheduler or in temporal nodes.
5. **Cache-key stability for static nodes.** Static-node cache keys produce the same digest they did before this branch and will continue to.
6. **Core library works without DB/Redis.** Filesystem-only backend is primary. Postgres/Redis remain optional, behind the FastAPI layer.
7. **No provider name branching in core/orchestration.** `core/` never says `if provider == "ollama"`. Provider selection rides on graph JSON params + plugin registry.
8. **Wait-free reads on the substrate.** No mutex on the read path. Writers publish new roots via CAS.
9. **Stenota is the integration canary.** Every phase has a "stenota still runs end-to-end" gate before merge.

## What's NOT in scope

- **Parallel window execution.** Scheduler v2 is single-worker-pool per role (hare, tortoise). Concurrent execution within a worker is a v3 problem.
- **Distributed scheduling.** Single-process for now. Multi-machine cooking is a much later concern.
- **CRDTs for multi-writer op-graph editing.** Op-graphs use single-writer publication. CRDTs are the right shape if/when multi-writer-without-coordination becomes a real requirement.
- **Frontend changes.** The React Flow editor will need updates for slots/strips/signals eventually, but the backend primitives stabilize first.
- **New graph JSON format version.** Temporal info, slot bindings, subscription declarations all ride on existing structures.

## How existing primitives map forward

| Today (feat/temporality) | Role in the final model |
|---|---|
| `TimeRange` | Same. Carried on every substrate event. |
| `WindowSpec` | Same. Op-graph-level node config. One of several cadence shapes (alongside `EventTriggeredWindow`, `AdaptiveWindow` if they land). |
| `AnnotationNode` | Same shape. Semantics shift from "cache poison" to "first-class evidence event read as input by re-cooking." |
| `AnnotationIndex` | Becomes a substrate projection (index over annotation events) rather than a separate dict. |
| `NodeCache` (Filesystem/InMemory) | Becomes the derivation cache projection over the substrate. Cache keys grow `op_graph_hash` + `version_set_hash`. |
| `CacheKey` | Same primitive. Six-tuple grows to eight (add op_graph + version_set). |
| `events.py` | Substrate log shape. Event types expand to cover op-graph publication, version-set publication, tag-ref updates, annotation lifecycle, etc. |
| `TemporalScheduler.run_batch` | Renamed `run_batch_oneshot` in Phase 5, kept as a convenience entry point that drives the event-driven core in batch configuration. |
| `ChunkedContext` | Same shape. `get_input_value` becomes strip-aware in Phase 4. |

## Branch strategy

- `feat/temporality` (this branch) — PR-n3 primitives. Merges to `main` when ready.
- `feat/substrate` — Phase 1–4 (substrate, op-graphs, slots, strips). Builds on `main` after `feat/temporality` lands.
- `feat/scheduler-v2` — Phase 5 (the new cooker).
- Subsequent phases get their own branches, each off `main` after the prior phase lands.

Stenota pins nodecules by git SHA. Bumping happens once per phase merge.

---

This roadmap is a living document. Update it when a phase lands, when scope shifts, or when reality reveals an assumption was wrong.
