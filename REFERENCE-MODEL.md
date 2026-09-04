# REFERENCE-MODEL.md — nodecules v2: declarative generation DAG over a COW node store

**Naming (2026-08-25, vault ADR-0020):** the system this document specifies
is **nodecules v2** — "nodecules" unqualified means this substrate; the
original engine on `main` is "the legacy engine". This file is the nodecules
v2 reference model; the working title "REFERENCE-MODEL" survives only as a
filename.

**Status:** Part I (the model) is design-only. Part II is design with
four pieces now shipped as code: **PR-r1** (`a40772c`) — the typed
access-pattern ADT — **PR-r2** (`ba9835a`) — the access-pattern
resolver — **PR-d1** — descriptions and the satisfies judgment (§22b)
— and **PR-d2** — placement, the plan as an artifact (§22c). The rest
of Part II is unbuilt.

Seventh draft. v6 baked in **scope** as a first-class structural
concept; v7 folds in **instance continuation / the settling spectrum**
— how IIR-style nodes (audio filters, recursive video filters, VLM
change-notices, rolling summaries) are recovered without an O(N²)
explosion — and syncs §5 / §24 to the code PR-r1 and PR-r2 actually
shipped.

Evolution:
- v1 (`2cd3233`) — functional dataflow with reachability cache.
- v2 (uncommitted) — spreadsheet metaphor. Discarded.
- v3 (`cd3367f`) — sparse-replica COW reference tree + excel DAG.
- v4 (`b4aec89`) — envelope/content split + external-leaf
  substitution + resurrection outcomes.
- v5 (`726def5`) — radical unification: flat node graph, kinds
  first-class, manifests as version anchors.
- v6 (`0e80dec`) — scope first-class; instance identity as scope;
  per-scope manifests; live inputs / sinks / examples PRs.
- v7 (this) — the settling spectrum (§17); §5 ADT synced to shipped
  PR-r1; PR plan synced to shipped PR-r1 + PR-r2.

---

# Part I — The declarative core

Read this part as the specification. Part II is implementation in
service of it.

## 1. Premise

**Everything is a node in an acyclic generation DAG.**

The DAG is *declarative*: a graph of nodes-and-edges that describes
what exists and what depends on what. Generation is the act of
producing a node's data by running its recipe over the nodes it
references.

No "cells with envelope and content sides." No "strips with cells."
No "templates separate from cells." All of those are kinds of nodes
— useful patterns, not separate concepts.

## 2. Nodes

A node has:

- **An id.** Unique within its scope (§4).
- **A kind.** Identifies what this node is for. First-class — see §3.
- **A scope.** Where the node lives in the scope hierarchy — see §4.
- **Data.** The payload. Bytes, a dict, a reference to fetchable
  content, recipe metadata, whatever the kind says. May be absent
  (described but not yet produced, or pruned).
- **Edges.** Typed references to other nodes — see §5.

Nodes are immutable: a "change" produces a new node. Versions are
captured in manifests (§7).

## 3. Kinds

A kind is a first-class entity. Each defines:

- **Schema** — what data instances hold.
- **Default retention** — how long instances persist by default.
- **Reference constraints** — which other kinds this kind's instances
  may reference (typed edges).
- **Recipe interface** — what a recipe for this kind takes as input
  and produces as output.

Kinds are themselves nodes (one bootstrap kind `Kind`; everything
else descends from it). Adding a new kind = adding a node. Versioning
a kind = producing a new kind node with a bumped version.

Common kinds (stenota-shaped, illustrative not exhaustive):

- `raw.audio`, `raw.video`, `raw.sensor` — leaf-ish; read from
  external resources.
- `raw.audio.live`, `raw.video.live` — live capture variants
  (mic, camera) bound to hardware.
- `asr.segment`, `diar.segment` — first-pass derived.
- `claim.L2.turn`, `claim.L2.action_item`, …, `claim.L4`, `claim.L5`
  — structured-claim nodes at every cooking level.
- `annotation.*` — user annotations.
- `envelope` — metadata describing how to rebuild another node.
  Separate node kind; not a property of other nodes.
- `recipe.template` — versioned recipe templates referenced by
  other nodes.
- `router.*` — pick among alternatives at generation time (§18).
- `sink.*` — output kinds (display widgets, audio playback, file
  writers, MCP broadcast).
- `scope` — see §4.

## 4. Scopes

A **scope** is a first-class entity that groups nodes by ownership,
lifecycle, and manifest sequence. Scopes form a hierarchy: every
scope (except the root) has a parent. Common shapes:

```
project/stenota                              ← root scope for stenota usage
├── project/stenota/meeting/abc123           ← a single meeting
│   └── …/instance/cooker-1                  ← a specific cooker run
├── project/stenota/library/recipes          ← shared recipe templates
└── project/stenota/library/persons          ← cross-meeting person registry

project/playground                           ← independent root
├── project/playground/instance/tab-1        ← one running playground tab
└── project/playground/instance/tab-2        ← another, independent
```

A scope has:

- **An id.** Globally unique across all scopes.
- **A parent scope** (or null for a root scope).
- **A label.** Human-readable name.
- **Its own manifest sequence** (§7). Each scope has independent
  versions and independent atomic commits.
- **A default retention policy** (may inherit from parent).

Nodes are scoped — every node id is unique *within its scope*.
Cross-scope edges are explicit: they cite the target scope as well
as the target node id (§5).

Why this matters:

- **Concurrent playground tabs.** Two tabs running the same graph
  against different mics get separate scopes; their cooked nodes
  don't collide. Cache keys are scope-qualified.
- **Multi-meeting state.** Per-meeting cooking lives in
  `meeting/abc123` scope. Cross-meeting state (person registry,
  shared recipes) lives in scopes higher up. Cross-meeting refs
  are visible in the data.
- **IIR / self-reference.** A node reading its own prior output
  resolves "me" relative to the cooker's scope. Different cookers
  see different selves; no risk of one cooker's continuation state
  polluting another's. (The settling mechanics are §17.)
- **Isolation for tests.** Running a graph in isolation = creating
  a fresh scope. No global state to clean up.

Scopes are *not* security boundaries (no authz at this layer; that's
deployment-tier concern). They're identity + lifecycle boundaries.

## 5. Edges

Edges are typed references between nodes. A node declares its edges
via **typed access patterns** — the ADT shipped in PR-r1
(`core/strip_access.py`):

```python
class Edge(BaseModel):
    target_id: NodeId
    target_scope: Optional[ScopeRef] = None   # None = same scope as source
    pattern: AccessPattern

# AccessPattern — the three shapes the real stenota graph uses,
# discriminated on a `kind` field:
class AllPattern      # whole-strip read (turns, participation, speaker_relabel)
class LatestPattern   # single most-recent element (asr/diar read audio.wav)
class RangePattern    # windowed read; (field, start: TimeExpr, end: TimeExpr)

# TimeExpr — symbolic, resolved at generation time:
class SelfWindowStart / SelfWindowEnd
class AbsoluteMs(ms >= 0)
```

PR-r1 deliberately shipped *three* patterns, not the seven v6 drafted.
The cut four (`Before`/`After`/`OrdinalAt`/`OrdinalRange`/
`SelfRelativeOrdinal`) were speculation — no node in the real stenota
graph used them. **`SelfPrevious`** — the IIR self-reference pattern
(`strip[me-1]`) — is deferred until the first settling windowed node
is built, and when added it ships *bundled* with the retention rule
in §17, never as a bare pattern.

`RangePattern` carries a `field` selector because a strip element can
hold more than one temporal attribute (a claim has `source_window`
and `time_ranges`); a windowed read must name which it tests.

The graph is **acyclic by construction**:

- Forward-time and forward-self references are rejected at the ADT
  level (the cut patterns and `SelfPrevious` would have carried that
  risk; the shipped three cannot express a forward reference).
- Cross-node cycles are rejected at graph-load by the cycle validator.
- **Cross-scope cycles** are similarly rejected — the validator
  walks the union graph (within + across scopes).

A node without typed access patterns falls back to dynamic
references at runtime, unanalyzable by static cycle detection.

Cross-scope edges are first-class: a meeting node may reference a
project-scoped recipe template; a playground instance may reference
a project-scoped example graph. The reference is explicit in the
edge structure.

## 6. Generation

Generation is one operation: **produce node X by running its recipe
over the nodes X references** (within and across scopes).

This single operation covers:

- **Cold start** — node X has data=None; generation produces data.
- **Stale recook** — node X has data, but a referenced node has
  bumped version; generation produces new data.
- **Resurrection** — node X had data, content was pruned;
  generation rebuilds from referenced nodes (which may themselves
  need resurrection, recursively).

Same machinery for all three; only the trigger differs (dirty
signal, GC sweep, scheduler tick).

A generation can have four outcomes:

1. **Exact** — every referenced node resolves, recipe is
   deterministic; result is byte-identical to a prior generation.
2. **Via substitute** — a routing node (§18) chose a different
   upstream than originally; result may differ in form but is
   semantically the same.
3. **Equivalent** — recipe is nondeterministic (unfixed-seed LLM,
   randomness) or referenced a different recipe version; result
   is *a* valid value, not the original.
4. **Lost** — some referenced node is unrecoverable and no
   substitute resolves. Returns a `Lost` sentinel.

For nodes that reference *their own* prior output (IIR / settling
nodes), the cost and recoverability of resurrection depend on where
the node sits on the settling spectrum — see §17.

## 7. Manifests anchor versions (per scope)

A **manifest** is a node that references (or indexes) every node
alive in a single scope at one version. Holding a manifest = holding
a wait-free snapshot of that scope. Each generation operation that
publishes new nodes also produces a new manifest *for the
affected scope*. Old manifests remain valid snapshots.

Each scope has its own "current" manifest pointer (advanced
atomically by CAS during transactions, §15). Readers holding old
manifests see old worlds within that scope; readers holding the
current manifest see latest. Multiple manifests can be alive
simultaneously without coordination.

Cross-scope reads compose: to traverse from a meeting-scoped node
through an edge into a project-scoped recipe template, the reader
either holds a manifest in each scope (a consistent snapshot
"across the system") or accepts the live-current of the target
scope (a "always read latest" semantic).

Manifests are themselves nodes. They carry metadata (timestamp,
author, transaction-id, semantic-label) and can be referenced from
other nodes (markers, §14).

## 8. That's the whole declarative model

Re-read §§1-7: nodes (with id, kind, scope, data, edges), kinds
(first-class), scopes (hierarchical, per-scope manifests), typed
edges (acyclic by construction, cross-scope explicit), generation
(one operation, four outcomes), manifests (version anchors per
scope). Everything else in this doc is implementation.

If something can be expressed as "a node of this kind in this
scope referencing those nodes" — that's where it goes. If it
can't, the model is incomplete and worth revisiting.

---

# Part II — Implementation: making it not horrific

The declarative core is principled. Running it on a MacBook Air
with finite RAM against a multi-hour meeting (or two concurrent
playground tabs streaming live audio) needs substantial engineering
behind it. This part is that effort.

## 9. COW substrate (per-scope)

The node store is a **sparse-replica copy-on-write persistent data
structure**, wait-free for examination. Each scope owns its own
COW structure; cross-scope refs are pointer-like edges between
otherwise-independent substrates.

- **Sparse** — only populated nodes take space; address space is
  unbounded.
- **Copy-on-write** — writes produce new versions with structural
  sharing of unchanged subgraphs. Old manifests remain valid.
- **Replica-friendly** — partial replicas coexist (RAM working set,
  on-disk subset, future distributed setups).
- **Wait-free for examination** — readers hold a manifest pointer
  and traverse without locks.

Implementation: HAMT (hash-array-mapped trie) per scope for v0.x;
revisit if profiling demands B-tree packing.

## 10. Refcount-driven retention

Retention is mechanical: **refcount on nodes**, with deterministic
finalization for the acyclic case (the DAG is acyclic by
construction).

Refs come from several sources:

- **System policy** — system holds refs per the per-kind retention
  policy.
- **User actions** — pinning takes a ref.
- **In-flight reads** — a reader holding a manifest implicitly
  holds refs to everything reachable from it (within scope and
  across scope refs).
- **Cross-references** — a node's outgoing edges count as refs
  into the targets (including cross-scope targets).

Retention "policy" is just *when the system releases its own
refs*. User / annotation / in-flight refs always override. If
anyone keeps a ref, the node sticks around.

When refcount hits zero, the node is finalized. Deterministic for
the acyclic shape; Python's refcount handles it.

## 11. Per-kind retention policies + staggered curves

Per-kind defaults; per-scope overrides allowed:

| Kind                        | Default retention                              |
|-----------------------------|------------------------------------------------|
| `raw.audio`                 | latest only (rebuild from media file)          |
| `raw.audio.live`            | latest only (live stream gone after capture)   |
| `raw.video`                 | latest only (rebuild from media file)          |
| `asr.segment`               | dense for last hour; daily checkpoints beyond  |
| `diar.segment`              | dense for last hour; daily checkpoints beyond  |
| `claim.L2.*`                | every version (small; expensive to recook)     |
| `claim.L3a`                 | dense recent + log-scale history               |
| `claim.L4`                  | every version forever (tiny; LLM-dependent)    |
| `annotation.*`              | every version forever (user authored)          |
| `envelope`                  | always (cheap; the rebuild slate)              |
| `recipe.template`           | always (other scopes still reference)          |
| `router.*` / `sink.*`       | scope-defined (UI sinks: latest only; archival sinks: forever) |
| `manifest`                  | staggered curve                                |

Retention density is a **curve, not a horizon**:

```
density of retained nodes at age
  ┃ ░░░░░░░
  ┃        ▓▓▓▓
  ┃            ████
  ┃                ████████
  ┗━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━► age
  now           1h        1d         1mo
```

Last hour: every node preserved. Last day: every 10 minutes. Last
month: daily. Older: weekly. Configurable per kind + per scope.
Cheap because structural sharing means N retained versions overlap
heavily.

A **settling node** (§17) overrides the curve for its own output:
it retains its last K cells unconditionally while it advances,
where K is its settle depth. That floor is not negotiable — see
the O(N²) explosion in §17.

## 12. Sparse load (RAM + disk)

Two tiers:

**RAM (hot working set).** Lazy load: tree internal nodes + leaf
nodes fetched from disk on read. Eviction: LRU on system-held refs.
**Lossless** — drop in-memory copy when refcount → 0; next read
reloads from disk. Bound configurable per scope.

**Disk (durable storage).** Per-kind retention rules drive which
on-disk nodes the system holds refs to. Refcount mechanics apply
equally to disk. Pruning is opportunistic and surfaceable: queued,
UI shows what will be freed, reversible until commit.

## 13. Indexes

The node store is fundamentally `{(scope, node_id) → node}`.
Indexes accelerate common queries:

- **Strip-position index** — `(scope, strip_name, time_range/ordinal)
  → node_id` for fast range queries.
- **Kind index** — `(scope, kind) → [node_id, ...]`.
- **Lineage index (reverse)** — `node → [nodes that reference it]`,
  built on demand.
- **Marker history** — manifests by marker (§14).
- **Cross-scope reference index** — for a given node, which scopes
  reference it. Useful for retention decisions ("this template is
  referenced by 50 active instances; don't prune").

Indexes can be reconstructed from the canonical `{id → node}`
store; they're caches, not sources of truth.

## 14. Markers as labeled manifests

A **marker** is a labeled reference to a manifest. Labels span
multiple axes: transaction id, semantic event, user action, recipe
library version bump, wall-clock checkpoint. Same manifest can wear
many labels. Markers are scoped: a marker labels a manifest within
a specific scope.

The history is navigable along whichever axis the user cares about
— "show me the last 5 transactions in this meeting" or "show me
what changed in the project when annotation X was added."

## 15. Transactions (per scope)

The COW substrate gives ACID for free when writes are batched
within a scope:

- Collect N node puts against the current manifest of the scope.
- Construct the new manifest (structural sharing with the old).
- CAS the scope's current-manifest pointer.

Properties: **atomic** (manifest pointer moves or it doesn't),
**consistent** (built against a single prior snapshot), **isolated**
(readers on the old see the old; readers on the new see the new;
wait-free for both), **durable** (persist new manifest before CAS).

Cross-scope transactions (touching nodes in multiple scopes
atomically) require coordination — multiple CAS on multiple scope
pointers. v0.x: avoid by structuring writes within a single scope
per transaction; cross-scope state changes go through normal
generation pipeline. v0.y+: real two-phase commit if needed.

A scheduler tick is naturally a per-scope transaction. Failures
roll back by discarding the in-progress manifest.

## 16. Pruning + resurrection (envelopes as rebuild slates)

When a node's content is pruned (retention policy released the
system's ref, no other ref holds it), the node may persist as a
slim **envelope** node of kind `envelope` — references the original
by id, holds recipe + input-version metadata, serves as the rebuild
instruction.

So: a content node and its envelope are two distinct nodes:

- `content:strips/turns/diarized#42` — the data dict.
- `envelope:strips/turns/diarized#42` — references content by id,
  holds recipe + inputs + state tag + cooked_at_ms.

They have different retention policies. Envelopes are cheap, kept
liberally. Content is expensive, pruned per the kind's curve. When
content is pruned but envelope remains, reads return
`Absent(rebuild_via=envelope_id)`. The UI / scheduler can trigger
resurrection.

For an IIR / settling node the content node carries two parts — the
public output and the private continuation state (§17). Both are
covered by the envelope's rebuild metadata.

This is the v4 envelope/content split as an **implementation
pattern**, not a conceptual one. In the model (§§1-8), they're
just two nodes referencing each other.

## 17. Instance continuation: the settling spectrum

An **IIR node** is one whose output at window N depends on its own
prior output. Naively resolved, that is O(N²): producing window N
recomputes N-1, which recomputes N-2… Whether a node can dodge that
— and whether it can be resurrected at all — is governed by one
axis: **settling depth**.

### Settling depth is a spectrum

How far back does a node's dependence on its own past actually
*matter*?

- **K = 0 — memoryless.** Most current stenota nodes (turns,
  participation, ASR run whole-file). No carried state.
- **small K — settles fast.** Audio IIR filters, recursive video
  filters (temporal denoise / motion smoothing), a recurrent VAD
  (Silero), quick VLM change-notices. The impulse response decays
  geometrically; after K windows the initial condition is forgotten.
- **large K — settles slowly**, but still finite.
- **K = ∞ — path-dependent.** Never settles; the influence of
  window 1 rides forward forever. Appended document layout (each
  element's position depends on all prior layout); a rolling
  *cumulative* LLM journal (a fact from hour 1 rides forward in the
  compressed summary indefinitely).

The common case is finite, usually small, K. **Genuinely
path-dependent (K = ∞) is exceedingly rare** — it is the "must
re-run serially on absolutely everything" case, and most things
people reach for (filters, change-notices, smoothers) are not it.

### Recoverability follows from K

| Class | Recover by | Cost | Retention |
|---|---|---|---|
| Settling (finite K) | cold-start, warm up K windows from any reasonable initial state; output from window K on is canonical | O(K) | retain last K output cells |
| Path-dependent (K=∞) + deterministic | faithful serial replay from t=0 | O(N) | optional — replayable |
| Path-dependent (K=∞) + nondeterministic | **cannot** — replay yields a *different* history | — | **pin the chain; correctness, not perf** |

For settling nodes, determinism barely matters — a deterministic
filter converges onto its true trajectory, a nondeterministic one
jitters within ε of it; either way the warmed-up output is treated
as interchangeable. Determinism is decisive **only at K = ∞**:
there it is the line between "faithfully replayable" and "the cell
chain is the only copy of the artifact."

So the functional divide is **recoverable vs not**, and the
not-recoverable set is exactly one quadrant — path-dependent ∧
nondeterministic. Everything else re-derives into something
interchangeable (settling) or identical (deterministic replay).

### The O(N²) explosion

A settling node's window N reads window N-1's output. If N-1's cell
is **retained** → O(1) read → O(N) for the strip. If N-1 was
**pruned** and must be resurrected, resurrection needs N-2, which
needs N-3 — every window triggers a full back-recursion: **O(N²)**.

The defense is the retention floor in §11: a settling node retains
its last K output cells unconditionally while it advances. The
`SelfPrevious` access pattern (deferred from PR-r1, §5) must
therefore ship **bundled** with this retention implication — the
access pattern and the retention rule are not independent. A bare
self-reference pattern is a loaded gun.

### Carried state is not always tidy data

The continuation state an IIR node hands forward can be a single
float (an EMA accumulator), a structured value (a rolling summary's
prior text), or an opaque tensor (a recurrent model's hidden state).
An IIR node's output cell therefore carries **two** things: the
public `output`, and the private `carried_state`. Downstream
consumers read `output`; the node's own next window reads
`carried_state`. The cell model (§16) accommodates this directly.

### Instance continuation proper

The strongest form keeps the node instance *and its carried state*
RAM-resident across the whole strip, checkpointing to a cell each
window for durability / resume. That is a constant-factor win on top
of the O(K) / O(N) floor — safe to treat as a pure cache, because
the carried state is always reconstructible from the last checkpoint
cell (settling) or by replay (deterministic path-dependent).

### Cook order

Settling and path-dependent nodes both cook forward (N-1 before N).
Settling nodes additionally tolerate **parallel / out-of-order**
cooking *if* each segment gets K windows of pre-roll. Path-dependent
nodes are strictly serial.

### Declaration

A node declares its place on the spectrum. `settling_windows` on
`NodeSpec` (PR-n7, previously unused) carries K for settling nodes;
the K warm-up windows are `phase=warmup` (DerivationPhase, PR-n6),
then `phase=canonical`. Path-dependent (K = ∞) needs its own marker
— `settling_windows` can't express ∞ (open question, §28).
`is_deterministic` (PR-n4) is the second axis, decisive only at
K = ∞. The substrate reads both and picks retention + cook-order +
resurrection straight from the table above.

## 18. Live-vs-file routing is graph structure

The proxy-edit pattern — cook from a live stream while recording to
a file, then later rebuild from the recording — is graph structure,
not leaf configuration. Three nodes:

- `raw.audio.live` — reads from `live://mic:0` when stream is open.
- `raw.audio.file` — reads from `media://meeting.mp4#audio[0..30s]`
  when file exists.
- `router.audio_source` — picks one of the above; downstream reads
  from this.

At cook time the router picks `live`. After the meeting, the router
picks `file`. The choice is encoded in the router's recipe (e.g.,
"prefer the lowest-latency available source"). The graph wires it
explicitly; nothing is hidden in configuration on a leaf.

Same pattern for fallbacks (`router.transcription_source` picks
among ASR providers), retries (`retry.*` kind wraps another node),
version migrations (`migrate.*` reads from prior-version nodes
during transitions). All are nodes you wire in, inspectable in the
graph.

External leaves hold a single `ResourceRef` pointing at durable
storage outside the node store. A `ResourceHandler` plugin keyed by
URI scheme fetches the bytes. The node store knows the *reference*,
not the bytes; the handler knows the bytes.

## 19. Live inputs (mic / camera / sensors)

Live-capture node kinds bridge hardware → node graph. Each is a
small plugin around an OS API:

- `raw.audio.live` — Core Audio (macOS) / PortAudio cross-platform.
  Recipe reads from `live://mic:<idx>` and emits PCM samples at a
  configured rate.
- `raw.video.live` — AVFoundation (macOS) / V4L2 (Linux). Reads
  from `live://camera:<idx>` and emits frames.
- `raw.sensor.imu` / `raw.sensor.gps` / etc. — platform-specific
  sensor APIs.

Live inputs are scope-local by default: a playground instance's mic
capture lives in that instance's scope. Two playground tabs
capturing from the same device have two independent capture nodes
(the resource handler can multiplex the actual hardware feed).

Live captures interact with retention specially: the live stream
itself is ephemeral. Retention applies to the *recorded form*
(when a parallel recording node fans out from the same source) or
to *derived nodes* (ASR segments, etc.). The `raw.audio.live` node
itself typically retains "latest only."

A live streaming model (Silero VAD, streaming ASR) run windowed is
a settling node — see §17; its hidden state is `carried_state`.

## 20. Output sinks

Sink kinds consume cooked node values and route them somewhere
observable:

- `sink.audio_playback` — play PCM samples.
- `sink.video_preview` — render frames to a UI widget.
- `sink.oscilloscope`, `sink.spectrogram` — live audio visualizers.
- `sink.text_display` — show structured-claim text in a panel.
- `sink.file_write` — append-mode JSONL or binary writer.
- `sink.mcp_broadcast` — publish cooked values to MCP clients
  (composes with stenota's existing MCP-as-toolbelt pattern).

Sinks are nodes like any other; they consume input edges and
produce side effects rather than returning data. The UI binds
matching React components to `sink.*` kinds — an oscilloscope
sink in the graph editor renders as an oscilloscope panel in the
playground view.

## 21. Diagnostics live outside the node store

Errors, logs, traces, perf metrics live in the **event log**
(`events.jsonl`), not in the node store. The store is *data*;
diagnostics are *about the process that produced data*.

A failed generation produces an event-log entry plus a `failed`
state tag on the affected envelope (with `error_event_id` pointing
into the event log). Event log is scoped — each scope has its own
event log, parented analogously to manifests.

Subscription consumers (`MEMORY.md` → `signals_and_slots.md`) tail
the event log; independent of node-store operations.

## 22. Concurrency

MVP: per-scope single-producer-per-node-id, batched transactions
within a scope.

- **Disjoint scopes.** Concurrent playground tabs in different
  scopes cook independently. No coordination needed.
- **Disjoint nodes within a scope.** Different recipes producing
  different nodes cook concurrently; writes touch disjoint leaves;
  CAS retry resolves trivially.
- **Same node within a scope.** Structurally absent (each node has
  one producer in the graph).
- **User pin vs system recook.** Per-node policy (pin wins).

Future (out of scope for v0.x):

- **Multi-instance live edit across scopes.** Two stenota processes
  editing the same project scope. Real OT/CRDT territory.
- **Cross-scope atomic transactions.** Two-phase commit across
  multiple scope manifests.
- **Redux-shaped action log.** Make the event log canonical;
  derive state from log replay.

## 22b. Descriptions and the satisfies judgment (PR-d1, shipped)

A **description** is a node that says what a job is — never how:

- `consumes` — strips the job reads, each with the access pattern it
  must be read with (the §5 ADT). This is the job's *valence*.
- `produces` — strips the job writes, each with a schema id.
- `tolerance` — a metric name and an acceptance line. **The metric ships
  with the description**, because deviation is job-shaped: ASR is scored
  by word error rate, diarization by a permutation-invariant DER, a colour
  transform by max-abs. Metrics are an open registry
  (`core/assay_metrics.py`).
- `reference` — the realization the description ships as its conformance
  oracle (the ingot's role, vault ADR-0014). Deviation is measured against
  it; it is the fallback when nothing cheaper passes.

A description's identity covers all four; its `name` is an alias and does
not hash — two names for one contract are one contract.

Binding a description is **two judgments** (vault ADR-0021, measured in
`spikes/matching-bench/`):

1. **Valence check** (structural): does a realization's `NodeSpec` declare
   reads compatible with `consumes` and writes covering `produces`? Decided
   without running anything. This is what *consumes* `reads_strip_patterns`
   and `writes_strips` — the declarations now have a reader. It provably
   cannot tell an impostor with an identical interface from the real thing.
2. **Assay** (empirical): score the realization's output against the
   reference's with the description's metric; accept iff within tolerance.
   The assay certifies the probed subset, never a universal claim, so its
   receipt — the **hallmark** — records probe provenance (`fixed-suite` /
   `fresh-drawn` / `workload`). A published suite is Goodhart-void as
   evidence; fresh workload-drawn probes detect defection at the harm
   rate (bench M7–M8, vault P-32).

A hallmark has **two independent axes**. *Identity* (`outcome`): `exact`
if the realization is the description's reference, `via-substitute`
otherwise. *Reproducibility*: `exact` only if the realization declares
itself deterministic (`NodeSpec.is_deterministic`) **and** measured zero
deviation on the probes; `equivalent` otherwise — including when nobody
said, because unknown determinism is perturbing (vault ADR-0009). A
bit-identical isomer is `via-substitute` + `exact`; a nondeterministic
reference re-run against itself is `exact` + `equivalent`. Absolute
reproducibility is achievable for some graph types — pure transforms,
seeded PRNGs, integer and symbolic pipelines, WASM ingots (deterministic
semantics by construction) — and it is rare. Where a graph type can have
it, a description demands it (`Tolerance.require_exact`): a realization
declaring nondeterminism fails the valence check, and one declaring
determinism that measures nonzero has falsified its own declaration — the
stated claim caught by the empirical one. This is how §6's four generation
outcomes decompose: `exact` / `equivalent` is the reproducibility axis,
`via substitute` the identity axis, `lost` the absence of a result on
either.

The decision is then policy over survivors — cheapest passing plan — and
the assay is applied *before* cost is consulted: the cheapest structurally
valid plan in the bench was the wrong one. No survivor is an answer, not
an error (E_NOINTERFACE). Every hallmark field is derivable from content
(`outcome` follows from `realization == reference`), so an independent
verifier can catch a forged receipt mechanically.

**Claims and hallmarks are decoration, not graph elements** (vault
ADR-0022). A `satisfies` claim cites a realization and a description by
identity and hashes into neither; attaching, revoking, or rewriting one
never perturbs the identity of the thing it is about. The direction is
enforced: a functional node may not read or write the `claims/`,
`hallmarks/`, or `vouches/` namespaces (`assert_functional`). Stated
decoration — grade, cost class, locality — lives on the *claim*, not the
description, because it describes the realization's offer.

Grounded on the first two real descriptions, drafted from stenota's ASR
and diarization nodes (`stenota_graph/contracts.py`): `asr/v1` (WER ≤ 0.15)
and `diarize/v1` (DER ≤ 0.20). What the grounding exposed: the nodes had
to be made statically describable first (they declared no strip access —
the first slice of PR-r11); both jobs sit in the `equivalent` regime
(neither model is byte-reproducible), so their receipts are always
tolerance-relative; and the tolerances are first cuts awaiting measured
decision margins (vault P-28). The description kind, claim kind, and
hallmark kind as *store* nodes wait on PR-r3/r4; today they are Pydantic
models over `NodeSpec`.

## 22c. Placement — the plan as an artifact (PR-d2, shipped)

Where compute runs is decided at **plan time, as data**, from four inputs:
a graph of jobs (each node names a description, §22b; edges say who feeds
whom), **executor advertisements** (locality — `on-device` / `lan` /
`cloud` — the claims each serves, per-realization cost, what is warm),
a **policy** (a lock level and what crossing between executors costs),
and the evidence the satisfies judgment needs. The output is a **plan**:
node → (executor, realization), the regions that induces, cost split into
compute and boundary crossings, and a reason for every choice and every
exclusion. It is content-addressed and re-verifiable, and it answers
"where did my data go" from its own record.

Three rules, each measured in `spikes/placement-bench/` on stenota's real
eleven-node graph:

- **Locks are planning constraints.** keyhole's lock levels become an
  admission predicate — `no-model-egress` excludes cloud executors,
  `full-airgap` everything but the device — applied *before* cost is
  consulted, with the reason recorded. An unsatisfiable policy fails at
  plan time with the job named; the plan is refused whole, because a
  partial plan silently drops work.
- **Binding granularity is a region** (vault ADR-0015). Per-node binding
  picks each job's cheapest executor and pays the crossings afterwards;
  region binding minimises compute plus crossings jointly (branch-and-
  bound over admissible executors — exhaustive, capped, a partitioner
  replaces it for large graphs). Data locality is a pin: a job whose
  input exists only on one executor (the media file, a live mic) runs
  there, and every other executor's exclusion says so.
- **Plans and executor records are decoration** (vault ADR-0022): they
  cite graphs, descriptions, and realizations by identity and are never
  cited back; `assert_functional` covers the `plans/` and `executors/`
  namespaces.

**The cost model** (founder, 2026-08-29): nodes and edges both cost, in
several dimensions, and edge costs compound. A node's compute, memory, and
bus use land on latency, energy, and money; costs are **vectors**
(`CostVector`) and the policy's **weights** fold them into the objective —
a phone weighs energy and thermals, a datacenter weighs money — so the
same graph plans differently under different biases. Memory is a capacity
*constraint*, not a sum. An edge that crosses a boundary pays by the
boundary's *kind* — `bus` between devices on one host, `lan` between
hosts, `wan` when the cloud is involved — a fixed part plus a per-byte
part, so data locality is a cost with the pin as its infinite case. And
crossing costs **compound**: a CPU → GPU → CPU ping-pong can be the
fastest plan node-by-node and the worst in practice, because bouncing
breaks pipelining and cache warmth. Those effects are **compound
heuristics declared as data** in the policy (`pingpong`,
`pipeline_fill_flush`), applied uniformly by the optimiser and reported by
name and location on the plan — system-design judgment encoded once, then
algorithmic. Every heuristic is a penalty, never a bonus, so the
branch-and-bound lower bound stays valid ("keep the pipeline together" is
expressed as "pay to break it").

**What the substrate commits to, and what it does not** (founder,
2026-08-29): the folded objective is one policy — the dumbest, on purpose.
Multi-dimensional cost does not reduce to a vector and a weighting: linear
scalarization misses the non-convex parts of the Pareto front, and once
edge effects compound, optimal aggregation is NP-hard in general. A system
may instead learn its own cost geometry — its own principal axes over
observed runs, or Least Volume Analysis (Chen, Diniz, Fuge,
arXiv:2404.17773: latent volume minimisation under a K-Lipschitz decoder,
PCA's ordering without its linearity), or a person's judgment. The
substrate's job is to make that possible by providing **identity** (every
plan, assignment, and crossing content-addressed, so an observation
attaches to exactly the thing that ran) and **observability** (measured
costs emitted as `Observation` records against those identities, in every
dimension, with what fired). So `plan_front` hands up the non-dominated
set with each plan's full vector and hash and lets the policy choose, and
`reconcile` lines observations up against what executors advertised — a
cost table is a stated claim, an observation is the empirical one, and the
gap recalibrates the advertisement. The aggregator is an upper layer.

Costs are declared today; the hardware runs replace them with measured
ones, and the plan's `verify` re-derives every number — vectors,
heuristics, and the folded objective — from the executor records and
policy it names. What is *not* here yet: cross-executor transfer of the
data itself (the store's sparse-replica tier, PR-r3), per-strip grant
scopes as constraints (vault P-13), and any executor actually running a
plan — this is the decision, not the dispatch.

---

# Part III — Working notes

## 23. What survives, what changed

Survives from earlier drafts unchanged:

- AccessPattern / TimeExpr ADT (v1 concept; shipped PR-r1).
- Static cycle detection (v1, extended in v5 + v6 for cross-scope).
- COW substrate + wait-free reads + structural sharing (v3).
- Refcount-driven retention (v4).
- Markers as labeled manifests (v4).
- Transactions as batched manifest updates (v4).
- Four resurrection outcomes (v4).
- Everything-is-a-node unification (v5).
- First-class kinds (v5).
- Scopes as first-class (v6).

New / changed in v7:

- **The settling spectrum** (§17). IIR-style nodes are placed on a
  K-axis (memoryless → fast-settling → slow-settling → path-
  dependent). Recoverability follows from K; determinism is decisive
  only at K = ∞. Carried-state-in-cell, the O(N²) explosion + its
  retention defense, cook-order rules.
- **§5 synced to shipped code.** PR-r1 shipped three access patterns
  (`All` / `Latest` / `Range`), not v6's drafted seven. `SelfPrevious`
  is deferred and retention-coupled.
- **PR plan synced** — PR-r1 and PR-r2 are shipped; PR-r2 was
  repurposed from cycle_validator extension to the access-pattern
  resolver (§24).

## 24. Examples as an importable library

The repo's `examples/` directory holds reference graph definitions:

```
examples/
├── audio/
│   ├── mic_to_speaker.json           # passthrough; tests live audio path
│   ├── mic_to_oscilloscope.json      # live capture + visualization
│   ├── mic_to_spectrogram.json
│   └── mic_to_asr_to_text.json       # mini stenota: ASR live
├── video/
│   ├── camera_preview.json           # live capture + display
│   └── camera_to_vlm_to_claims.json  # frame sampling + LLM
├── stenota/
│   └── full_meeting_pipeline.json    # the canonical stenota graph
└── README.md                          # what each example demonstrates
```

The graph editor exposes an **"Import example"** action: opens a
file picker over `examples/`, loads the chosen JSON into the editor
as a new graph. Users edit, fork, save under their own names; the
example file remains the canonical reference. Not auto-installed
as a registered graph; not blessed; just a starter set.

This decouples "what graphs ship with nodecules" from "what graphs
a user has built." The runtime treats user-imported graphs and
example-derived graphs identically — they're all just graphs.

Example graphs live in `project/examples` scope (or similar) in
the node store so they version cleanly; users importing them get
their own copies in their own scopes.

## 25. PR plan

Shipped:

### PR-r1: typed access-pattern ADT — SHIPPED (`a40772c`)
`core/strip_access.py`: `TimeExpr`, `AccessPattern` (`All` / `Latest`
/ `Range`), `StripAccess`; `NodeSpec.reads_strip_patterns`. Grounded
against all nine stenota nodes; three patterns, not seven. 25 tests.

### PR-r2: access-pattern resolver — SHIPPED (`ba9835a`)
`core/strip_resolve.py`: `resolve_time_expr`, `resolve_range`,
`range_matches`. Repurposed from the v6-planned cycle_validator
extension, which was found near-empty (within-window cycles are
detectable from strip names alone, and the grounded ADT has no
past/future-direction patterns to exploit). 14 tests.

### PR-d1: descriptions + the satisfies judgment — SHIPPED
`core/descriptions.py`: `Description` (consumes / produces / tolerance /
reference, content-addressed), `SatisfiesClaim` and `Hallmark` as
decoration, `valence_check`, `assert_functional`, `run_assay`, `decide`,
`verify_hallmark`. `core/assay_metrics.py`: `max_abs`, `wer`, `der`,
open registry. Grounded on stenota's ASR and diar nodes (§22b). 41 tests.

### PR-d2: placement — the plan as an artifact — SHIPPED
`core/placement.py`: `Executor`, `Job` (with data-locality pins),
`PlacementGraph`, `Policy` (lock level + boundary costs), `candidates`
(locks, then the satisfies judgment), `plan` (per-node baseline vs
region branch-and-bound), `data_flow`, `verify_plan`. Measured on
stenota's real graph in `spikes/placement-bench/` (§22c). 18 tests.

Unbuilt:

### PR-r3: Node store substrate (per scope)
HAMT-backed; wait-free reads; CAS atomic writes; sparse load.
Per-scope manifest sequences; scope ids in node addressing.
`get_node(scope, id) → Optional[Node] | Absent | Lost`.
**Must be IIR-aware:** cells carry `(output, carried_state)` (§17).
~1400 + 900 LOC.

### PR-r4: Kind system
First-class kinds. Schemas, retention, ref constraints, recipe
interfaces. Runtime kind addition. ~400 + 300 LOC.

### PR-r5: Generation engine
"Produce node X" as a unified primitive. Dirty propagation,
resurrection, cold-start all routed through it. Four-outcome
return. **Must be IIR-aware:** settling-node warm-up resurrection
(§17). ~600 + 500 LOC.

### PR-r6: Retention policies + refcount mechanics
Per-kind retention curves. System refs vs user/in-flight refs.
Disk pruning. **Must be IIR-aware:** the keep-last-K retention
floor for settling nodes (§17). ~400 + 300 LOC.

### PR-r7: Indexes
Strip-position, kind, lineage (reverse), marker history,
cross-scope reference index. ~600 + 500 LOC.

### PR-r8: Markers + transactions
Marker kind, labeled manifest refs. ACID via CAS. ~300 + 250 LOC.

### PR-r9: External resources + router nodes
`ResourceRef` + `ResourceHandler` plugin interface. Per-scheme
handlers. Router kinds; generic `retry.*`, `migrate.*` patterns.
~600 + 500 LOC.

### PR-r10: Diagnostics formalization
Event-log API surface, `failed` state, error-event refs. Per-scope
event logs. ~150 + 150 LOC.

### PR-r11: Stenota migration
Stenota nodes declare access patterns, scope appropriately, write
through the node store. Cooking levels become kind declarations.
~800 + 400 LOC.

### PR-r12: Live input node kinds
`raw.audio.live` (Core Audio / PortAudio), `raw.video.live`
(AVFoundation / V4L2), `raw.sensor.*` placeholders. Each is a
plugin wrapping an OS API. Multiplexing logic in `ResourceHandler`
for shared hardware. ~600 + 300 LOC across plugins.

### PR-r13: Output sinks + UI widgets
`sink.*` node kinds + matching React components (oscilloscope,
spectrogram, video preview, text display, audio playback). The UI
binds widgets to sinks by kind. ~700 + 400 LOC across backend +
frontend.

### PR-r14: Examples library + import action
`examples/` directory at repo root with starter graphs. "Import
example" UI action in the editor. ~200 LOC + a handful of JSON files.

### PR-r15: SelfPrevious access pattern + IIR wiring
The deferred IIR self-reference pattern (§5, §17). Ships *bundled*
with the keep-last-K retention rule and the forward-cook
requirement — never as a bare pattern. Lands when the first
settling windowed node is built to ground it (a recursive video
filter or a windowed Silero node is the likely first). ~250 +
300 LOC.

## 26. Relationship to deferred PRs

- **PR-n7b (scheduler-v2).** Hare/tortoise = two cookers in the
  same scope, each holding its own manifest, writes producing new
  manifests, scope's tree merging via CAS. PR-r3 + PR-r5 + PR-r8
  give it the substrate. The settling-spectrum cook-order rules
  (§17) are scheduler-v2's pre-roll logic.
- **PR-n9b (concrete LLM adapters).** Orthogonal; not blocked.
- **PR-s2b (stenota ctx.strip migration).** Subsumed by PR-r11.

## 27. Hard invariants

- **Everything is a node.** No bespoke types for cells, envelopes,
  recipes, annotations, routers, sinks, scopes, manifests.
- **The graph is acyclic.** Forward-time / forward-self rejected
  at ADT construction; cross-node cycles rejected at graph-load,
  including cross-scope cycles.
- **Nodes are immutable.** Changes produce new nodes; versions
  captured in manifests.
- **Scopes are first-class.** Every node lives in exactly one
  scope; cross-scope edges are explicit.
- **Reads are wait-free.** Manifest = snapshot; traversal never
  blocks on writers.
- **Manifests are per-scope.** Cross-scope transactions are
  coordinated explicitly, not implicit.
- **Refcount rules retention.** Policy = when the system releases
  its own refs. User / annotation / in-flight refs override.
- **IIR access patterns are retention-coupled.** A self-reference
  pattern (`SelfPrevious`) cannot ship without the keep-last-K
  retention floor for the strip it reads. An uncoupled one is the
  O(N²) explosion (§17).
- **Diagnostics live outside the node store.** Per-scope event
  logs.
- **Routing is graph structure.** Substitution, fallback, retry,
  migration — all nodes, not flags.
- **Decoration never bonds into functional nodes.** Claims, hallmarks,
  and vouches cite realizations and descriptions by identity; nothing
  functional reads or writes them, so a label can never perturb the
  identity of what it labels (§22b).
- **Live inputs are scope-local.** Two scopes capturing the same
  hardware get independent capture nodes (resource handler
  multiplexes).
- **The declarative model (Part I) is the spec.** Part II changes
  if memory or compute needs evolve; Part I doesn't.

## 28. Open questions

- **Sources as strips.** Media inputs (files, live streams) are
  conceptually strips: each contained stream is a strip of frames /
  samples / packets, with the source itself a parent grouping them.
  "Global" source properties (overall extent, container-level
  metadata) are strips with a single element spanning the whole time
  window — the strip metaphor degrades gracefully into the
  single-element case. Cross-stream alignment within a source, and
  cross-source alignment with clock-drift modeling, are properties
  of the source / parent node, not file-format characteristics read
  ad-hoc at use sites. Legacy / inferred-extent containers (AVI-style
  files where stream durations are absent and must be derived from
  packet counts or container hints) need first-class support — the
  inferred-vs-labeled distinction is part of what the source node
  carries. **Current stopgap:** `stenota_graph` CLI's
  `_duration_ms_from_container` is a band-aid hierarchy over PyAV
  characteristics, explicitly labeled — it disappears when a
  `source.media` kind lands.
- **Node id format.** Within-scope ids: kind-prefixed
  (`envelope:strips/...`) or opaque? Per user input: kind is first-
  class, so likely an opaque id + kind field, with prefixed naming
  as a convention for human readability.
- **Path-dependent marker.** `settling_windows` carries finite K
  for settling nodes; it can't express K = ∞. Path-dependent nodes
  need a distinct marker — a sentinel value, or a companion
  `path_dependent: bool`. The substrate uses it to pick serial cook
  + chain-pin retention (§17).
- **Carried-state cell shape.** §17 says an IIR cell's content is
  `(output, carried_state)`. Is `carried_state` a sub-field of the
  content dict, or a separate node the output edges to? Likely a
  sub-field (it travels with the output), but a separate node would
  let retention prune `output` while keeping `carried_state`. TBD
  when PR-r15 grounds it.
- **Cross-scope read consistency.** When a node in scope A reads
  from scope B, does it pin to B's manifest at the time of A's
  read, or always read B's latest? Probably: explicit choice in
  the edge spec (`consistency: snapshot | latest`).
- **Scope deletion.** Deleting a scope cascades to all its nodes.
  But cross-scope refs from other scopes still point in. Probably:
  refuse scope deletion if cross-scope refs exist, OR convert
  affected nodes to `Lost`.
- **Failed generations.** Auto-retry policy per kind? Probably
  retry-with-backoff for transient failures (network errors);
  user action for persistent failures.
- **Marker GC.** When a marker has no labeled queries and no
  pinned hold, drop it. Standard refcount.
- **Scope identity persistence.** What's the on-disk format for a
  scope id? Probably UUIDs internally + human labels for display.
- **Hardware multiplexing for live inputs.** Two scopes both
  capturing `live://mic:0` — does the resource handler open one
  hardware stream and fan out, or open two? Almost certainly
  fan-out (one hardware stream, multiple subscribers); needs
  reference counting at the handler level.
- **Substrate concurrency model.** Single-process MVP: CAS per
  scope. Multi-process: filesystem locking per scope or a
  coordinator. Out of scope until multi-process is real.

## 29. Connections to MEMORY entries

- `abstraction_discipline.md` — no provider-name branching. Same
  principle: no kind-name or scope-kind branching inside
  operations; structure parameterizes behavior.
- `structural_over_policy.md` — flag-based policy in shared-state
  loops is a smell. COW substrate + node graph + scopes makes
  flag-discriminated logic unrepresentable.
- `mutability_is_figurative.md` — wet/drying/dry/smudged as UI
  tag. State tags live on envelope nodes.
- `annealing_not_constraints.md` — labels are observations.
- `signals_and_slots.md` — subscriptions tail per-scope event logs.
- `chunk_size_principle.md` — node granularity is whatever a kind
  defines.
- `project_state_layer.md` — explicitly resolved here: project
  state is just a higher-up scope in the hierarchy.

## 30. tl;dr

**Part I (the model):** everything is a node in an acyclic
generation DAG. Nodes have kinds (first-class) and live in scopes
(hierarchical, first-class). Edges are typed references, including
explicit cross-scope refs. Manifests anchor versions *per scope*.
Generation = produce a node by running its recipe over referenced
nodes. That's it.

**Part II (the implementation):** sparse-replica COW substrate
per scope, wait-free reads, refcount retention with per-kind
staggered curves, sparse load from disk, indexes for ergonomic
queries, markers as labeled manifests, transactions as batched
manifest updates within a scope, pruning + resurrection via
envelope kinds, the settling spectrum for IIR nodes, routing /
live inputs / output sinks as graph nodes, diagnostics
side-channel via per-scope event logs.

**The settling spectrum (v7).** IIR nodes sit on a K-axis:
memoryless → fast-settling (audio/video filters, VLM change-
notices) → slow-settling → path-dependent. Settling nodes recover
by K-window warm-up and need only the last K cells retained;
path-dependent nodes split on determinism (replayable vs
unrecoverable). Genuinely path-dependent is rare; most real IIR
work settles fast.

**Shipped:** PR-r1 (access-pattern ADT) and PR-r2 (resolver) are
code. PR-r3 (the COW node store substrate, per-scope manifests,
IIR-aware cells) is the load-bearing next PR.

**MVP boundary:** per-scope single-producer-per-node + COW +
sparse load + batched per-scope transactions. No CRDT, no OT, no
cross-scope atomic transactions, no merging beyond disjoint writes
within a scope.

If parts of the framing feel off, flag in chat before any code.
