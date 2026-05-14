# REFERENCE-MODEL.md — declarative generation DAG over a COW node store

**Status:** design proposal, no code yet. Sixth draft. v5 unified
everything as a node in an acyclic generation DAG (Part I) and
separated implementation concerns (Part II); v6 bakes in **scope**
as a first-class structural concept — resolving v5's §23
multi-meeting open question, supporting concurrent instance
identity (playground tabs, parallel cookers, IIR self-reference)
without collapsing cache keys, and giving the existing runtime a
clean integration point.

Evolution:
- v1 (`2cd3233`) — functional dataflow with reachability cache.
- v2 (uncommitted) — spreadsheet metaphor. Discarded.
- v3 (`cd3367f`) — sparse-replica COW reference tree + excel DAG.
- v4 (`b4aec89`) — envelope/content split + external-leaf
  substitution + resurrection outcomes.
- v5 (`726def5`) — radical unification: flat node graph, kinds
  first-class, manifests as version anchors.
- v6 (this) — scope first-class; instance identity as scope;
  manifests per scope; cross-scope refs explicit; three new PRs for
  live inputs, output sinks, and importable examples.

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
- `router.*` — pick among alternatives at generation time (§17).
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
- **IIR / self-reference.** A node reading `strip[me - 1]` resolves
  "me" relative to the cooker's scope. Different cookers see
  different selves; no risk of one cooker's IIR state polluting
  another's.
- **Isolation for tests.** Running a graph in isolation = creating
  a fresh scope. No global state to clean up.

Scopes are *not* security boundaries (no authz at this layer; that's
deployment-tier concern). They're identity + lifecycle boundaries.

## 5. Edges

Edges are typed references between nodes. A node declares its edges
via **typed access patterns** (the ADT from v1, unchanged):

```python
class Edge(BaseModel):
    target_id: NodeId
    target_scope: Optional[ScopeRef] = None   # None = same scope as source
    pattern: AccessPattern

class AccessPattern:
    Latest
    Range(start: TimeExpr, end: TimeExpr)
    Before(at: TimeExpr) / After(at: TimeExpr)
    OrdinalAt(index: IndexExpr) / OrdinalRange(start, end: IndexExpr)
    SelfRelativeOrdinal(offset: PositiveInt)

class TimeExpr:    # symbolic; resolved at generation time
    SelfWindowStart / SelfWindowEnd
    SelfWindowStart - Duration / SelfWindowEnd - Duration
    AbsoluteMs(int)

class IndexExpr:
    SelfElementIndex
    SelfElementIndex - PositiveInt
    AbsoluteIndex(int)
```

The graph is **acyclic by construction**:

- Forward-time and forward-self references are rejected at the ADT
  level.
- Cross-node cycles are rejected at graph-load by an extended cycle
  validator (builds the `(node, time_class)` DAG, rejects cycles in
  the `present`-only subgraph).
- **Cross-scope cycles** are similarly rejected — the cycle validator
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
2. **Via substitute** — a routing node (§17) chose a different
   upstream than originally; result may differ in form but is
   semantically the same.
3. **Equivalent** — recipe is nondeterministic (unfixed-seed LLM,
   randomness) or referenced a different recipe version; result
   is *a* valid value, not the original.
4. **Lost** — some referenced node is unrecoverable and no
   substitute resolves. Returns a `Lost` sentinel.

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

This is the v4 envelope/content split as an **implementation
pattern**, not a conceptual one. In the model (§§1-8), they're
just two nodes referencing each other.

## 17. Live-vs-file routing is graph structure

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

## 18. Live inputs (mic / camera / sensors)

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

## 19. Output sinks

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

## 20. Diagnostics live outside the node store

Errors, logs, traces, perf metrics live in the **event log**
(`events.jsonl`), not in the node store. The store is *data*;
diagnostics are *about the process that produced data*.

A failed generation produces an event-log entry plus a `failed`
state tag on the affected envelope (with `error_event_id` pointing
into the event log). Event log is scoped — each scope has its own
event log, parented analogously to manifests.

Subscription consumers (`MEMORY.md` → `signals_and_slots.md`) tail
the event log; independent of node-store operations.

## 21. Concurrency

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

---

# Part III — Working notes

## 22. What survives, what changed

Survives from earlier drafts unchanged:

- AccessPattern / TimeExpr / IndexExpr ADT (v1).
- Static cycle detection (v1, extended in v5 + v6 for cross-scope).
- COW substrate + wait-free reads + structural sharing (v3).
- Refcount-driven retention (v4).
- Markers as labeled manifests (v4).
- Transactions as batched manifest updates (v4).
- Four resurrection outcomes (v4).
- Everything-is-a-node unification (v5).
- First-class kinds (v5).

New in v6:

- **Scopes as first-class.** Hierarchical, with per-scope manifest
  sequences. Resolves v5 §23.
- **Cross-scope edges.** Explicit references; cycle validator
  walks the union graph.
- **Live input node kinds.** `raw.audio.live`, `raw.video.live`,
  `raw.sensor.*`. §18.
- **Output sink node kinds.** `sink.audio_playback`,
  `sink.video_preview`, `sink.oscilloscope`, `sink.spectrogram`,
  `sink.text_display`, `sink.file_write`, `sink.mcp_broadcast`.
  §19.
- **Examples library** — importable, not baked-in. §23.

## 23. Examples as an importable library

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

## 24. PR plan (revised for v6)

Adding three PRs for the playground / live-runtime work; minor
tweaks to r3/r4 for scope mechanics.

### PR-r1: AccessPattern ADT
Unchanged across all drafts. ~300 + 150 LOC.

### PR-r2: cycle_validator extension
Now also handles cross-scope cycles. ~200 + 300 LOC.

### PR-r3: Node store substrate (per scope)
HAMT-backed; wait-free reads; CAS atomic writes; sparse load.
**Per-scope manifest sequences; scope ids in node addressing.**
Single API: `get_node(scope, id) → Optional[Node] | Absent | Lost`.
~1400 + 900 LOC.

### PR-r4: Kind system
First-class kinds. Schemas, retention, ref constraints, recipe
interfaces. Runtime kind addition. ~400 + 300 LOC.

### PR-r5: Generation engine
"Produce node X" as a unified primitive. Dirty propagation,
resurrection, cold-start all routed through it. Four-outcome
return. ~600 + 500 LOC.

### PR-r6: Retention policies + refcount mechanics
Per-kind retention curves. System refs vs user/in-flight refs.
Disk pruning. ~400 + 300 LOC.

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

### PR-r12: Live input node kinds (NEW)
`raw.audio.live` (Core Audio / PortAudio), `raw.video.live`
(AVFoundation / V4L2), `raw.sensor.*` placeholders. Each is a
plugin wrapping an OS API. Multiplexing logic in `ResourceHandler`
for shared hardware. ~600 + 300 LOC across plugins.

### PR-r13: Output sinks + UI widgets (NEW)
`sink.*` node kinds + matching React components (oscilloscope,
spectrogram, video preview, text display, audio playback). The UI
binds widgets to sinks by kind. Existing API endpoints extend with
streaming subscriptions for live data. ~700 + 400 LOC across
backend + frontend.

### PR-r14: Examples library + import action (NEW)
`examples/` directory at repo root with starter graphs. "Import
example" UI action in the editor. Examples are loaded into the
user's scope, not the example scope; the user fork-edits-saves.
~200 LOC + a handful of JSON files.

## 25. Relationship to deferred PRs

- **PR-n7b (scheduler-v2).** Hare/tortoise = two cookers in the
  same scope, each holding its own manifest, writes producing new
  manifests, scope's tree merging via CAS. PR-r3 + PR-r5 + PR-r8
  give it the substrate.
- **PR-n9b (concrete LLM adapters).** Orthogonal; not blocked.
- **PR-s2b (stenota ctx.strip migration).** Subsumed by PR-r11.

## 26. Hard invariants

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
- **Diagnostics live outside the node store.** Per-scope event
  logs.
- **Routing is graph structure.** Substitution, fallback, retry,
  migration — all nodes, not flags.
- **Live inputs are scope-local.** Two scopes capturing the same
  hardware get independent capture nodes (resource handler
  multiplexes).
- **The declarative model (Part I) is the spec.** Part II changes
  if memory or compute needs evolve; Part I doesn't.

## 27. Open questions

- **Node id format.** Within-scope ids: kind-prefixed
  (`envelope:strips/...`) or opaque? Per user input: kind is first-
  class, so likely an opaque id + kind field, with prefixed naming
  as a convention for human readability.
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

## 28. Connections to MEMORY entries

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

## 29. tl;dr

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
envelope kinds, routing / live inputs / output sinks as graph
nodes, diagnostics side-channel via per-scope event logs.

**Scope is the missing piece from v5.** Resolves multi-meeting
identity, concurrent playground tabs, IIR self-reference, test
isolation — all the same mechanism. A scope is just a node with a
parent and its own manifest sequence; cross-scope refs are edges
that name a target scope.

**MVP boundary:** per-scope single-producer-per-node + COW +
sparse load + batched per-scope transactions. No CRDT, no OT, no
cross-scope atomic transactions, no merging beyond disjoint writes
within a scope.

**Concrete next step:** PR-r1 (the AccessPattern ADT). Unchanged
across all six drafts. PR-r3 (the COW node store substrate with
per-scope manifests) is the load-bearing PR.

If parts of the framing feel off, flag in chat before any code.
