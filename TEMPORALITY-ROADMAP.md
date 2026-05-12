# Temporality Roadmap

What lands after `feat/temporality` (PR-n3) and why, with the order driven by
stenota's near-term milestones rather than a clean-slate architecture vision.
Living doc — update when reality reveals an assumption was wrong.

Read `TEMPORALITY.md` first — it's the design of the primitives already on this
branch (`TimeRange`, `WindowSpec`, `AnnotationIndex`, `NodeCache`, `events.py`,
`TemporalScheduler.run_batch`).

## Recognition that shapes everything below

**Stenota has already organically built much of the architecture this doc
describes.** The work in front of us is mostly *extracting and formalizing*
patterns that already exist in `stenota_graph/` and `stenota/core/sidecar.py`,
plus filling a few real gaps (eviction, subscriptions, IIR pre-roll). It is
not "build a new substrate from scratch." Concretely, the existing patterns:

| Pattern | Where it lives today | Future role |
|---|---|---|
| **Cache vs archive split** | Sidecar layout: `cache/` vs `claims/*.jsonl`. `RenderMarkdownNode` does an on-graph-close finalize that appends to `claims/L2.jsonl`. | Generalize via `is_deterministic` on `NodeSpec`. |
| **Strips by another name** | Edges in `stenota.v0.json` are "marker ports" (`turn_count`, `ready`); actual data flows through sidecar JSONL files addressed by convention. | Make addressing first-class via a strip naming layer. |
| **Pluggable function as graph** | `LLMToolLoopNode` instantiated as `name_inference`, `lens_exec_summary`, `lens_hot_takes` via `NodeData.parameters`. | Recognize: this IS the slot abstraction. No separate `slot_name@v1` machinery needed today. |
| **Capability injection** | `ToolContext(sidecar, annotation_index, node_params)` in `stenota_graph/tool_registry.py`. | Generalize into nodecules' `Environment`. |
| **Annotations as data AND cache-key contributors** | `speaker_relabel.py` reads `ctx.annotation_index` for display names; `supports_reanneal=True` smudges its cache. Both paths live. | No change. The dual semantics are correct. |
| **LLM-proposes / user-accepts authority gate** | `propose_speaker_name` writes to `insights/proposed_speaker_names.jsonl`; `accept_speaker_proposal` promotes to a user-source AnnotationNode. | Pattern worth lifting as a registered idiom. |
| **Tool-driven re-cooking** | `auto_anneal` is a write tool that re-runs clustering, then merges by label. LLM agents invoke it via `LLMToolLoopNode`. | This IS the operating-graph mutation pattern. Robot self-modification = same shape, different graph. |
| **Self-modification via library embedding** | `stenota-mcp` exposes the full tool registry + preview-control over MCP stdio. | A robot consuming stenota as a library uses the same surface. |

**Implication:** the "build a separate substrate, run it on top of the
sidecar" plan I floated earlier was overengineering. The sidecar IS the
substrate; the JSONL files are the event log; the cache directory IS the
derivation projection; the `@tool` registry IS the capability layer. We
formalize, generalize, and fill gaps — we don't replace.

## What stenota does NOT yet have (the real gaps)

1. **`cache/` is never evicted.** Grows unboundedly. No LRU, no size cap, no
   TTL. Needed.
2. **No `is_deterministic` flag on nodes.** The cache-vs-archive split is
   maintained by hand via the on-graph-close finalize pattern. Works for
   summarizers; doesn't generalize.
3. **No strip API.** Consumers go through `iter_jsonl(sc / CLAIMS_L2, ...)` +
   filter by hand. Cycle prevention is by convention, not validation.
4. **No subscription / push API.** v0.2 attention-requests would have to poll
   without it.
5. **No IIR pre-roll / settling-time concept.** No node today is IIR-shaped,
   but as soon as one is (rolling notes feeding sectional summaries feeding
   rolling notes), the scheduler has no pre-roll machinery.
6. **`run_batch` enumerates windows up front.** Can't host an event-driven
   live loop. Stenota v0.5+ live mode needs the rewrite.
7. **Provider adapter is chat-shaped.** No tool-use, no JSON-schema. Stenota's
   `agent.py` calls Ollama directly; the docstring already says "future: add
   provider adapters; only `_invoke_chat` changes."
8. **`ExecutionContext` carries env via `execution_inputs["sidecar_path"]`.**
   Every node redoes the same lookup. Typed env declarations are absent.

## Phases, in stenota-milestone order

Each phase is one or more PRs labeled `PR-n<N>-<slug>`. Each lands additively
and passes the static-DAG regression suite + stenota's smoke run as a gate.

### PR-n4-strips — Strip naming + lazy iterator API

Strips as a **naming convention** over the existing sidecar JSONL files plus a
lazy iterator API. No new storage. Maps directly to stenota's L0–L4 hierarchy:

- `strips/audio/raw` → `audio.wav`
- `strips/asr/segments` → `asr.jsonl`
- `strips/diar/segments` → `diar.jsonl`
- `strips/turns/diarized` → `claims/L2.jsonl` filtered by `kind=="turn"`
- `strips/claims/L3a@5min` → `claims/L3a_5min.jsonl`

API:

```python
strip = ctx.strip("strips/turns/diarized")
prev_turn = strip[me - 1]
recent = strip[me - 5 : me]
in_window = strip.in_range(time_range)
prior_section = strip.before(my_time_range)
```

Indexing has two flavors: absolute (`strip[42]`, `strip[-1]`) and self-relative
(`strip[me - 1]`). Forward indexing within own strip rejected at graph load.
Cross-strip forward reads forbidden by temporal monotonicity.

Cycle validator walks declared strip dependencies at graph instantiation.

### PR-n5-cache-evict — Cache vs archive, with eviction

`is_deterministic: bool` on `NodeSpec` (default True for backward compat, but
LLM-bound stenota nodes set it False). The cache layer respects it:

- Deterministic node → `cache/` (content-addressed JSON, evictable). Re-derive
  on miss.
- Nondeterministic node → *must* declare an archive destination (e.g.
  `claims/L2.jsonl`); cache miss does NOT silently regenerate — it's a real
  miss because the original output can't be reproduced.

Eviction policy on `cache/`: LRU + size cap, configurable per sidecar. Default
cap reasonable for the meeting case; robot lifelong sessions configure higher
or disable.

Stenota's existing on-graph-close `RenderMarkdownNode` becomes one specific
use of this pattern, not the only mechanism.

### PR-n6-subs — Strip subscriptions (push API)

The push counterpart to the pull API from PR-n4. A subscriber registers
interest in a strip; the scheduler / writer notifies on matching events.
Default visibility: canonical-phase events at time ≤ subscriber's `now`.
Explicit configuration scopes:

```python
sub = ctx.subscribe(
    "strips/attention.requests",
    visibility=Visibility(
        phases={Phase.CANONICAL},
        time_horizon=lambda now: TimeRange(now - 5*MIN, now),
    ),
)
async for evt in sub:
    handle(evt)
```

Unblocks stenota v0.2 attention-requests: L2 summarizer publishes to
`strips/attention.requests`; VLM sampler subscribes; default visibility means
the sampler reacts to requests that have been emitted, not future ones.

Internal scheduler readiness uses the same mechanism in PR-n7: a windowed
node "is ready" iff its upstream strip subscriptions have fired.

### PR-n7-scheduler-v2 — Event-driven cooker

Replaces `run_batch`'s batch-startup enumeration with an event-driven loop:

- **Dirty queue** as first-class scheduler input. Tags name why something
  needs cooking (`stale-data`, `stale-graph`, `stale-annotation`,
  `pending-reanneal`, `served`, `blocking`). External events (annotations
  landing, graph edits) write tags.
- **Hare worker** drains `{blocking, stale-data}` at head-of-stream priority.
- **Tortoise worker** drains the rest under budget.
- **Phase-aware emission**: derivations carry `phase ∈ {warmup, canonical,
  superseded}`. "Filter just reset" semantics — covers cold-start, processor
  swap, op-graph swap, smudge.
- **Cold-start vs resume signal**: nodes that have efficient empty-state
  initialization (conversational context, clustering, etc.) declare it and
  skip the zero-fill convergence.
- **Pre-roll via history reach**: cooker reaches back through sidecar JSONL
  to prime IIR state. Zero-fill is the universal fallback when history
  doesn't extend far enough.
- **Three modes, one engine**: single-shot (no windows, no queue — today's
  static DAG), continuous low-latency (hare-only), offline high-latency
  (both workers, initial flood of stale).

This is the largest single PR. Rename `run_batch` → `run_batch_oneshot` here
(now justified by the API change anyway). Stenota's existing v0.1 pipeline
runs identically under the new scheduler as a gate.

### PR-n8-env — Typed Environment

Disentangle `ExecutionContext` into three things:

- **`Environment`** (read-mostly ambient capabilities): substrate root,
  providers, clock, annotation index, sidecar path. Declared on `NodeSpec` as
  `reads_env=["substrate", "sidecar", "llm.default", "time"]`.
- **Append-only sinks**: archive appenders, signal emitters, log sinks.
  Declared as `writes_env=["strips/claims/L2", "signals.attention"]`.
- **Per-execution context**: outputs being accumulated, errors, status. Stays
  writable as today.

Stenota's `ToolContext(sidecar, annotation_index, node_params)` is the
proof-of-concept; this generalizes the pattern to all nodes (not just
tool-loop agents).

Scoped overrides: a subgraph can override `llm.default` for its interior
without affecting the parent. Per-graph, per-subgraph, per-node granularity.

The runtime refuses to instantiate a node whose env deps aren't satisfied —
failures move from "NoneType has no attribute X" to clean instantiation-time
errors. `stenota_graph/nodes/_common.py:get_sidecar_path` raising at runtime
becomes a graph-load-time check.

### PR-n9-providers — Tool-aware provider adapter

Extend `core/smart_context.py`'s `BaseProviderAdapter` to handle tool-use +
JSON-schema-constrained output:

```python
async def generate_with_tools(
    self,
    messages: list[dict],
    tools: list[ToolSchema],
    schema: dict | None = None,
    **kwargs,
) -> tuple[ToolCallResponse, ...]
```

Four current adapters (Ollama, Anthropic, Bedrock, Mock) grow tool support.
Stenota's `stenota_graph/agent.py:LensAgent._invoke_chat` becomes the
provider-swap point that the docstring already promised. Same
`LLMToolLoopNode` works against Ollama, Claude, and Bedrock with only a
provider param change.

Also: JSON-schema-constrained mode (matches Ollama's `format=schema`,
Anthropic's structured outputs, Bedrock's tool-use schema). Stenota's
`stenota_lite/summarize.py` JSON-schema retry loop becomes a one-line
`schema=_CLAIMS_JSON_SCHEMA` argument.

### What's deferred (probably indefinitely)

- **Separate persistent in-memory substrate.** Sidecar + lazy JSONL iter +
  per-process annotation index is sufficient. If/when a robot session truly
  exceeds what a single sidecar can hold, revisit; not before.
- **Op-graphs + version sets as substrate events.** Stenota already publishes
  versioned graphs as JSON files (`stenota.v0.json`). The git-shaped lineage
  + tag-as-slot machinery is real-world useful at robot-self-modification
  scale; for the meeting case, file-as-version is fine.
- **Full slot contract versioning.** `node_type` strings + the param-override
  mechanism (already in the scheduler) cover the pluggable-function case for
  now. Slot contracts become formal when we have heterogeneous third-party
  processors competing for the same slot — a robot ecosystem concern, not a
  meeting concern.
- **CRDTs for multi-writer op-graph editing.** No multi-writer requirement
  exists.
- **Distributed scheduling.** Single-process for now.
- **Wait-free in-memory persistent data structures (HAMT etc.).** Pyrsistent
  is slow; we don't need it. JSONL append + atomic file replace + readers
  holding file paths gives us most of the snapshot semantics for free.

## Hard invariants across every phase

1. **Static-DAG execution path does not regress.** Every existing node,
   example graph, and test passes before and after every phase. The 136
   temporal tests on `feat/temporality` and the static-DAG tests on `main`
   are the regression suite.
2. **Stenota's `stenota_lite/` stays nodecules-free.** It's the schema /
   perf control group. Any nodecules API a stenota-graph node adopts must
   have a corresponding stenota-lite-friendly equivalent.
3. **Schema compatibility between stenota-lite and stenota-graph.** Both
   paths produce byte-for-byte compatible sidecars. Don't break this.
4. **Stenota's `Confidence`, `StructuredClaim`, `Mutability`, `AnnotationRef`
   schemas in `stenota/core/models.py` are the canonical form.** Nodecules
   doesn't redefine these.
5. **`nodecules.core.time.TimeRange` and `stenota.core.models.TimeRange` stay
   structurally identical but distinct types.** Boundary conversion is
   explicit (`SummarizerL2Node` already does it). Don't try to unify.
6. **Time is integer milliseconds, meeting-relative.**
7. **`TimeSource` injected, never global.**
8. **Cache-key stability for static deterministic nodes.** No semantic shifts
   to existing static-node keys.
9. **Core library works without DB/Redis.** Filesystem + Pydantic, no
   Postgres required for the library import.
10. **No provider name branching in `core/` or orchestration.** Provider
    selection rides on params + the adapter registry. PR-n9 reinforces this.
11. **Tortoise/hare both write new derivation events, never overwrite.** Old
    derivations stay live for readers holding earlier roots.

## Branch strategy

- `feat/temporality` → `main` after a sign-off pass. This roadmap doc rides
  in.
- Each PR-n<N> off `main` after the prior phase merges.
- Stenota pins nodecules by git SHA, bumped once per phase merge.
- Branch graveyard inevitable; cleaned at each major milestone.

## Coordination with stenota's roadmap

| Stenota milestone | Required nodecules support |
|---|---|
| v0.2 (VLM + attention-request queue + L3a + observability) | PR-n4 strips, PR-n5 cache-evict, PR-n6 subs |
| v0.3 (`AnnotationNode` re-anneal + Tauri UI) | PR-n7 scheduler-v2 (re-anneal under tags) |
| v0.4 (L3b + L4 + packaging) | PR-n8 env, PR-n9 providers (so Claude/Bedrock work cleanly) |
| v0.5 (live mode) | PR-n7 already covers it; live = `WallClock` + hare-only worker |
| robot integration (months out) | No additional nodecules work expected. Robot embeds `stenota` as a library and consumes the same MCP-shape registry. |

If the robot integration reveals genuinely-new requirements we haven't
anticipated, that's a v2 nodecules conversation — not a Phase 9 of this
roadmap.
