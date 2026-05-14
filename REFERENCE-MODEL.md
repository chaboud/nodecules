# REFERENCE-MODEL.md — declarative generation DAG over a COW node store

**Status:** design proposal, no code yet. Fifth draft. Each prior
draft refined the framing; v5 separates the *declarative core*
(short, principled) from the *implementation layer* (long,
pragmatic). The model is radically unified: everything is a node in
an acyclic generation DAG. Everything else is engineering to make
that tractable.

Evolution:
- v1 (`2cd3233`) — functional dataflow with reachability cache.
- v2 (uncommitted) — spreadsheet metaphor. Discarded.
- v3 (`cd3367f`) — sparse-replica COW reference tree + excel DAG.
- v4 (`b4aec89`) — envelope/content split + external-leaf
  substitution + resurrection outcomes.
- v5 (this) — radical unification: flat node graph, kinds
  first-class, manifests as version anchors, "envelope" demoted from
  conceptual split to implementation pattern, external-leaf
  substitution rewritten as graph-node routing.

---

# Part I — The declarative core

The whole conceptual surface fits on a page. Read this part as the
specification; Part II is the implementation in service of it.

## 1. Premise

**Everything is a node in an acyclic generation DAG.**

That is the model. No "cells with envelope and content sides." No
"strips with cells." No "templates separate from cells." All of
those are kinds of nodes — useful patterns, not separate concepts.

The DAG is *declarative*: a graph of nodes-and-edges that describes
what exists and what depends on what. Generation is the act of
producing a node's data by running its recipe over the nodes it
references.

## 2. Nodes

A node has:

- **An id.** Globally unique within the graph.
- **A kind.** Identifies what this node is for (raw audio? L2 turn?
  annotation? envelope? recipe template? router?). First-class —
  see §3.
- **Data.** The payload — bytes, a dict, a reference to fetchable
  content, recipe metadata, whatever the kind says it should hold.
  May be absent (the node exists as a description; its data is yet
  to be produced or has been pruned).
- **Edges.** References to other nodes. Outgoing edges are the
  node's declared dependencies — "to produce my data, the recipe
  reads these other nodes."

Nodes are immutable: a "change" produces a new node. Versions are
captured in manifests (§6).

## 3. Kinds

A kind is itself a first-class entity. Each kind defines:

- **Schema.** What data instances of this kind hold.
- **Default retention.** Policy for how long instances of this kind
  persist by default.
- **Reference constraints.** What kinds this kind's instances may
  reference (typed edges).
- **Recipe interface.** What a recipe for this kind takes as input
  and produces as output.

Kinds are themselves nodes — meta-level recursion stops at a single
"Kind" kind. Adding a new kind = adding a node. Versioning a kind =
producing a new kind node with a bumped version. The C++ template
analogy holds: kinds parameterize what shape concrete nodes take.

Common kinds (stenota-shaped, illustrative not exhaustive):

- `raw.audio`, `raw.video`, `raw.sensor` — leaf-ish nodes reading
  from external resources.
- `asr.segment`, `diar.segment` — first-pass derived nodes.
- `claim.L2.turn`, `claim.L2.action_item`, `claim.L2.decision` —
  structured-claim nodes.
- `claim.L3a`, `claim.L3b`, `claim.L4`, `claim.L5` — roll-ups at
  various horizons.
- `annotation.*` — user annotations of various flavors.
- `envelope` — metadata describing how to rebuild another node.
  Separate node kind; not a property of other nodes.
- `recipe.template` — versioned recipe templates referenced by
  other nodes.
- `router.audio_source`, `router.video_source` — nodes that pick
  among alternatives at generation time (§16).

## 4. Edges

Edges are typed references. A node declares its edges via **typed
access patterns** (the ADT from v1, unchanged):

```python
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
    # SelfWindowEnd + Duration intentionally absent (forward-time rejected)

class IndexExpr:
    SelfElementIndex
    SelfElementIndex - PositiveInt    # n-1, n-2, ...
    AbsoluteIndex(int)
    # SelfElementIndex + anything intentionally absent (forward-self rejected)
```

The graph is **acyclic by construction**: forward-time and
forward-self references are rejected at the ADT level; cross-node
cycles are rejected at graph-load by an extended cycle validator
(builds the `(node, time_class)` DAG, rejects cycles in the
`present`-only subgraph).

A node without typed access patterns falls back to dynamic
references at runtime. Those nodes participate in the graph but
don't get static cycle guarantees.

## 5. Generation

Generation is one operation: **produce node X by running its recipe
over the nodes X references.**

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
2. **Via substitute** — a routing node (§16) chose a different
   upstream than originally; result may differ in form (proxy vs
   master media) but is semantically the same.
3. **Equivalent** — recipe is nondeterministic (unfixed-seed LLM,
   randomness) or referenced a different recipe version; result
   is *a* valid value, not the original.
4. **Lost** — some referenced node is unrecoverable and no
   substitute resolves. Returns a `Lost` sentinel.

## 6. Manifests anchor versions

A **manifest** is a node that references (or indexes) all nodes
alive at one version. Holding a manifest = holding a wait-free
snapshot of the world. Each generation operation that publishes new
nodes also produces a new manifest. Old manifests remain valid
snapshots.

The system has a "current" manifest pointer (advanced atomically by
CAS during transactions, §14). Readers holding old manifests see
old worlds; readers holding the current manifest see latest.
Multiple manifests can be alive simultaneously without coordination.

Manifests are *themselves* nodes. They can carry their own metadata
(timestamp, author, transaction-id, semantic-label) and be
referenced from other nodes (markers, §13).

## 7. That's the whole declarative model

Re-read §§1-6: nodes (with id, kind, data, edges), kinds (first-
class), typed edges (acyclic by construction), generation (one
operation, four outcomes), manifests (version anchors). Everything
else in this doc is implementation.

If something can be expressed as "a node of this kind referencing
those nodes" — that's where it goes. If it can't, the model is
incomplete and worth revisiting.

---

# Part II — Implementation: making it not horrific

The declarative core is principled. Running it on a MacBook Air
with a finite RAM budget against a multi-hour meeting needs a
substantial engineering effort behind it. This part is that effort.

## 8. COW substrate

The node store is a **sparse-replica copy-on-write persistent data
structure**, wait-free for examination:

- **Sparse** — only populated nodes take space; address space is
  unbounded.
- **Copy-on-write** — writes produce new versions with structural
  sharing of unchanged subgraphs. Old roots remain valid.
- **Replica-friendly** — partial replicas coexist (RAM working
  set, on-disk subset, future distributed setups).
- **Wait-free for examination** — readers hold a manifest pointer
  and traverse without locks.

Same shape as Clojure persistent collections, immer.js, Git's
object DB, ZFS snapshots. Implementation: HAMT (hash-array-mapped
trie) for v0.x; revisit if profiling demands B-tree-style packing.

## 9. Refcount-driven retention

Retention is mechanical: **refcount on nodes**, with deterministic
finalization for the acyclic case (the DAG is acyclic by
construction; Python's refcount handles it cleanly).

Refs come from several sources:

- **System policy.** The system holds refs to nodes per the
  retention policy (recent N manifests, pinned nodes, project-state
  cells, per-kind rules — see §10).
- **User actions.** A user pinning a node takes a ref.
- **In-flight reads.** A reader holding a manifest implicitly
  holds refs to everything reachable from it.
- **Cross-references.** A node's outgoing edges count as refs into
  the nodes referenced.

Retention "policy" is just *when the system releases its own
refs*. User / annotation / in-flight refs always override policy —
if anyone keeps a ref, the node sticks around.

When refcount hits zero, the node is finalized. Deterministic for
the acyclic shape (no GC cycles to break). Python's refcount + a
weak-secondary-index pattern handles it without explicit bookkeeping.

## 10. Per-kind retention policies + staggered curves

Different node kinds have wildly different cost/value:

| Kind                        | Default retention                              |
|-----------------------------|------------------------------------------------|
| `raw.audio`                 | latest only (rebuild from media file)          |
| `raw.video`                 | latest only (rebuild from media file)          |
| `asr.segment`               | dense for last hour; daily checkpoints beyond  |
| `diar.segment`              | dense for last hour; daily checkpoints beyond  |
| `claim.L2.*`                | every version (small; expensive to recook)     |
| `claim.L3a`                 | dense recent + log-scale history               |
| `claim.L4`                  | every version forever (tiny; LLM-dependent)    |
| `annotation.*`              | every version forever (user authored)          |
| `envelope`                  | always (cheap; the rebuild slate)              |
| `recipe.template`           | always (other nodes still reference old versions)|
| `manifest`                  | staggered (see below)                          |

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
month: daily. Older: weekly. Configurable per kind. Cheap because
structural sharing means N retained versions overlap heavily.

Per-kind policy combined with envelope-separate-from-content
(§15) means we can drop expensive content while keeping cheap
envelopes — the rebuild slate stays even after the cooked output
is gone.

## 11. Sparse load (RAM + disk)

Two tiers:

### RAM (hot working set)

- Lazy load: tree internal nodes + leaf nodes fetched from disk on
  read.
- Eviction: LRU on system-held refs. **Lossless** — drop in-memory
  copy when refcount → 0; next read reloads from disk.
- Bound: configurable (e.g., `~/.stenota/config.toml`:
  `cache.ram_mb = 256`).

### Disk (durable storage)

- The node store is the on-disk format; sidecars are its serialized
  form.
- Pruning is real: disk fills up. Per-kind retention rules drive
  which on-disk nodes the system holds refs to.
- Refcount mechanics apply equally to disk: if no in-RAM ref, no
  pinned ref, no manifest ref, no edge from a retained node — the
  on-disk node can be pruned.
- Pruning is opportunistic and surfaceable: queued, UI shows
  "about to free 1.2GB," reversible until commit. Not a silent
  background sweep.

## 12. Indexes

The node store is fundamentally `{node_id → node}`. Indexes
accelerate common queries:

- **Strip-position index.** For nodes in a time-indexed strip,
  arranged by `(strip, time_range)` for fast range queries. This
  is the "tree" from v3/v4 — now demoted to one index among many.
- **Kind index.** "All nodes of kind=X." Cheap to maintain.
- **Lineage index.** Forward (a node's outgoing edges) is intrinsic.
  Reverse (a node's incoming edges — "what depends on this?") is a
  secondary index maintained on demand.
- **Marker history.** Manifests by marker (§13).

Indexes can be reconstructed from the canonical `{id → node}`
store; they're caches, not sources of truth. New indexes can be
added without migrating data.

## 13. Markers as labeled manifests

A **marker** is a labeled reference to a manifest. Labels span
multiple axes:

- Transaction id (every batched commit)
- Semantic event ("ASR window 12 settled", "user accepted speaker
  name proposal")
- User action ("undo point auto-set before destructive edit")
- Recipe library version bump
- Wall-clock checkpoint ("save every N seconds")

Same manifest can wear many labels. The history is navigable along
whichever axis the user cares about. UI: "show me the last 5
transactions" or "show me what changed when annotation X was
added" — both are queries over marker → manifest → diff.

Markers themselves are nodes (kind=`marker`). They hold refs to
manifests, which keeps those manifests alive. Marker GC: when a
marker is no longer relevant (the kind's retention curve drops it),
it's released; the manifest stays alive only if something else
references it.

## 14. Transactions

The COW substrate gives ACID for free when writes are batched:

- Collect N node puts against the current manifest.
- Construct the new manifest (structural sharing with the old).
- CAS the current-manifest pointer to publish.

Properties:

- **Atomic** — manifest pointer moves or it doesn't.
- **Consistent** — the new state was built against a single prior
  snapshot.
- **Isolated** — readers on the old manifest see the old world;
  readers on the new see the new. Wait-free for both.
- **Durable** — persist the new manifest before CAS.

A scheduler tick is naturally a transaction: produce everything
dirty, batch all new nodes, CAS once. Failures roll back by
discarding the in-progress manifest (just drop local construction;
the published pointer never moved).

Same-node concurrent writes are absent in the MVP because each
node has one producer (the recipe + position determines the
writer). Multi-instance live edit would re-open this; see §18.

## 15. Pruning + resurrection (envelopes as rebuild slates)

When a node's content data is dropped (retention policy released
the system's ref, and no other ref holds it), the node itself
*may* persist as a slim **envelope** — a separate node of kind
`envelope` that references the original by id, holds the recipe +
input-version metadata, and serves as the rebuild instruction.

So: a content node and its envelope are two distinct nodes:

- `content:strips/turns/diarized#42` — the data dict.
- `envelope:strips/turns/diarized#42` — references the content
  node's id, holds recipe + inputs + state tag + cooked_at_ms.

They have different retention policies. Envelopes are cheap, kept
liberally. Content is expensive, pruned per the kind's curve. When
content is pruned but envelope remains, `get_node(content_id)`
returns `Absent(rebuild_via=envelope_id)`. The UI / scheduler can
trigger resurrection from the envelope.

This is the v4 "envelope/content split" *as an implementation
pattern*, not a conceptual one. In the declarative model
(§§1-7), they're just two nodes referencing each other. In the
implementation, the separation buys cheap tombstones.

Envelopes themselves are produced by recipes — the act of
generating a content node also produces (or updates) the envelope
node. That keeps the meta-level uniform: envelopes are generated
like any other kind.

## 16. Live-vs-file routing is graph structure

The proxy-edit pattern — cook from a live stream while recording
to a file, then later rebuild from the recording — is **graph
structure**, not leaf configuration. Three nodes:

- `raw.audio.live` — kind=raw.audio, reads from `live://mic:0` when
  the stream is open.
- `raw.audio.file` — kind=raw.audio, reads from
  `media://meeting.mp4#audio[0..30s]` when the file exists.
- `router.audio_source` — kind=router.audio_source, picks one of
  the above. Downstream nodes reference the router, not the leaves.

At cook time the router picks `live`. After the meeting ends, the
router picks `file`. The choice is encoded in the router node's
recipe (e.g., "prefer the lowest-latency available source"). The
graph wires it explicitly; nothing is hidden in configuration on a
leaf.

Same pattern handles:

- **Fallbacks.** `router.transcription_source` picks among multiple
  ASR providers in priority order.
- **Retries.** A `retry.*` kind wraps another node, retrying its
  generation on failure with backoff.
- **Version migrations.** A `migrate.*` kind reads from
  prior-version nodes and emits new-version nodes during transition
  periods.

All of these are nodes you wire in, inspectable in the graph,
override-able by graph editing. None of them are flags-on-leaves.

External leaves (`raw.audio.live`, `raw.audio.file`,
`raw.video.file`, …) hold a single `ResourceRef` pointing at
durable storage outside the node store. A `ResourceHandler` plugin
keyed by URI scheme fetches the bytes when the recipe runs. The
node store knows the *reference*, not the bytes; the handler
knows the bytes.

## 17. Diagnostics live outside the tree

Errors, logs, traces, perf metrics live in the **event log**
(`events.jsonl`), not in the node store. The store is *data*;
diagnostics are *about the process that produced data*. Different
audience, different lifecycle (often longer-lived than the data;
sometimes scrubbed for privacy).

A failed generation produces an event-log entry plus a `failed`
state tag on the affected envelope node (with an `error_event_id`
field pointing into the event log for details). Subscription
consumers (`MEMORY.md` → `signals_and_slots.md`) tail the event
log; independent of node-store operations.

## 18. Concurrency

MVP: single-process, single-producer-per-node-id, batched
transactions.

- **Disjoint producers.** Different recipes producing different
  nodes cook concurrently; writes touch disjoint leaves; CAS retry
  resolves trivially.
- **Same node, concurrent producers.** Structurally absent (each
  node has one producer defined by the graph).
- **User pin vs system recook.** Per-node policy (pin wins), not
  a merge.

Future (out of scope for v0.x):

- **Multi-instance live edit.** Two stenota processes editing the
  same project. Real OT / CRDT territory; merge functions per kind.
  Substrate accommodates; designs deferred.
- **Redux-shaped action log.** Make the event log canonical; derive
  state from log replay. Compatible (manifests = log positions);
  adds write latency.

---

# Part III — Working notes

## 19. What survives from earlier drafts

From v1 / v3 / v4:

- **AccessPattern / TimeExpr / IndexExpr ADT** — unchanged. §4
  uses it directly.
- **Static cycle detection extension** — unchanged.
- **Structural-determinism predicate** — derived from declared
  inputs + presence of external-effect tags on the recipe kind.
- **COW substrate + wait-free reads + structural sharing** —
  unchanged.
- **Excel DAG processing** — unchanged at the implementation
  level; absorbed into §5 generation at the model level.
- **Refcount-driven retention** — unchanged.
- **Markers as labeled manifests** — unchanged from v4.
- **Transactions as batched manifest updates** — unchanged from v4.
- **Four resurrection outcomes** — unchanged from v4.

Replaced in v5:

- **"Cell" as a first-class object** → nodes. Cell is just a
  position in a strip's index, not a structural concept.
- **Envelope/content as two get-paths on the same id** → two
  distinct node ids (envelope is a kind, references content by
  id). Different retention per kind, naturally.
- **External-leaf primary+substitute config** → router nodes in
  the graph. v4 §6's `ExternalLeafRef.substitutes` removed.
- **"Address tree" as primary structure** → strip-position index
  (one of many indexes over the node store). v4 §1's tree-shape
  privileged language softened.

## 20. PR plan (revised for v5)

The graph naturally extends to more PRs because the model is
more unified — but each PR is also smaller because there's less
cross-cutting bespoke logic.

### PR-r1: AccessPattern ADT (unchanged from v1)

`AccessPattern`, `TimeExpr`, `IndexExpr` in `core/strip_access.py`.
`NodeSpec.reads_strip_patterns: list[StripAccess] = []`. Pydantic
round-trip; static rejection at construction. No scheduler / cache
/ stenota changes. ~300 lines + ~150 lines of tests.

### PR-r2: cycle_validator extension

Use typed access patterns where declared; coarse same-strip check
where not. ~150 lines + ~250 lines of tests.

### PR-r3: Node store substrate

`core/node_store.py` — HAMT-backed; wait-free reads; CAS atomic
writes; sparse load from disk. `Node`, `NodeId`, `Manifest`
types. The single API is `get_node(id) -> Optional[Node] |
Absent | Lost`. No envelope/content distinction at the API level;
that emerges from `kind`. ~1200 lines + ~800 lines of tests.

### PR-r4: Kind system

First-class kinds. `Kind` is itself a node kind. Schema
definitions, default retention, reference constraints, recipe
interfaces. Allows runtime kind addition. ~400 lines + ~300 lines
of tests.

### PR-r5: Generation engine

The "produce node X" operation as a unified primitive. Dirty
propagation, resurrection, cold-start all routed through it.
Four-outcome return type. ~600 lines + ~500 lines of tests.

### PR-r6: Retention policies + refcount mechanics

Per-kind retention curves. Refcount accounting (system refs vs
user/in-flight refs). Disk pruning. Envelope-kind retention
distinct from content-kind retention. ~400 lines + ~300 lines of
tests.

### PR-r7: Indexes

Strip-position, kind, lineage (reverse), marker history. Each as
a separate module composed at startup. ~500 lines + ~400 lines of
tests.

### PR-r8: Markers + transactions

Marker kind, labeled manifest references. Transaction batching;
ACID via CAS. ~300 lines + ~250 lines of tests.

### PR-r9: External resources + router nodes

`ResourceRef` + `ResourceHandler` plugin interface. Per-scheme
handlers (`media://`, `live://`, `sensor://`, etc.). Router kinds
(`router.audio_source`, `router.transcription_source`, …) and
generic `retry.*`, `migrate.*` patterns. ~600 lines + ~500 lines
of tests.

### PR-r10: Diagnostics formalization

Event-log API surface, `failed` envelope state, error-event refs.
Mostly documentation + a small wrapper. ~100 lines + ~100 lines
of tests.

### PR-r11: Stenota migration

Stenota nodes declare access patterns, write through the node
store, consume from it. Cooking levels (L2 turns, L3a 5-min, etc.)
become kind declarations. ASR/diar wrappers become specific node
kinds. ~800 lines + ~400 lines of tests.

The total is bigger than v4's plan but more uniform: each PR is a
clean slice of the same model. The substrate (r3) is still load-
bearing.

## 21. Relationship to deferred PRs

- **PR-n7b (scheduler-v2).** Hare/tortoise = two cookers running
  against the same node graph, each holding its own manifest,
  writes producing new manifests, tree merging via CAS. PR-r3 +
  PR-r5 + PR-r8 give it the substrate.
- **PR-n9b (concrete LLM adapters).** Orthogonal; not blocked.
- **PR-s2b (stenota ctx.strip migration).** Subsumed by PR-r11.

## 22. Hard invariants

- **Everything is a node.** No bespoke types for cells, envelopes,
  recipes, annotations, routers. All are kinds of nodes.
- **The graph is acyclic.** Forward-time and forward-self refs
  rejected at ADT construction; cross-node cycles rejected at
  graph-load.
- **Nodes are immutable.** Changes produce new nodes; versions
  captured in manifests.
- **Reads are wait-free.** Holding a manifest = holding a snapshot;
  traversal never blocks on writers.
- **Refcount rules retention.** Policy = when the system releases
  its own refs. User / annotation / in-flight refs override.
- **Diagnostics live outside the node store.** Event log;
  side-channel.
- **Routing is graph structure.** Substitution, fallback, retry,
  migration — all nodes, not flags.
- **Transactions are batched manifest updates.** ACID via single
  CAS per commit.
- **The declarative model in Part I is the spec.** Part II
  changes if memory or compute needs evolve; Part I doesn't.

## 23. Open questions

- **Node id scheme.** Kind-prefixed (`envelope:strips/...`),
  opaque-with-kind-field, or convention-based? Per user input:
  kind is first-class, so likely a field on the node + opaque ids,
  with kind-prefixed naming as a convention for human readability.
- **Kinds-of-kinds.** Is `Kind` a kind? Probably yes
  (meta-uniform); the recursion terminates at one fixed
  bootstrap kind `Kind`.
- **Failed generations.** Auto-retry policy per kind? Probably yes
  for transient failures (e.g., network errors); explicit user
  action for persistent failures.
- **Multi-meeting scoping.** Project-state nodes live above any
  single meeting. Likely a `scope: "meeting" | "project"` field
  on nodes, with separate manifests per scope, cross-scope refs
  allowed.
- **Substrate concurrency model.** Single-process: CAS is enough.
  Multi-process: filesystem-level locking or coordinator. Out of
  scope until multi-process is a real requirement.
- **Marker GC.** When a marker has no labeled queries and no
  pinned hold, drop it. Standard refcount.
- **External resource discovery for already-cooked nodes.** A node
  was cooked live-only and no substitute existed at the time; user
  later attaches a recording. Probably: add a new router upstream
  via a graph edit + smudge the affected nodes.
- **HAMT vs B-tree vs LSM.** HAMT for simplicity; revisit if
  profiling demands.

## 24. Connections to MEMORY entries

- **`abstraction_discipline.md`** — no provider-name branching.
  Same principle: no kind-name branching inside operations; kinds
  parameterize behavior via their declared interface.
- **`structural_over_policy.md`** — flag-based policy in loops on
  shared state is a design smell. COW substrate + node graph
  makes flag-discriminated logic unrepresentable.
- **`mutability_is_figurative.md`** — wet/drying/dry/smudged as UI
  tag. Reinforced: state tags live on envelope nodes, observable,
  no separate scheduler state.
- **`annealing_not_constraints.md`** — labels are observations,
  not constraints. Reinforced: pin flags are user affordances on
  individual nodes.
- **`signals_and_slots.md`** — subscription layer tailing the
  event log. Compatible: subscriptions watch the event log
  side-channel.
- **`chunk_size_principle.md`** — chunk size is tractability, not
  granularity. Compatible: node granularity is whatever a kind
  defines.

## 25. tl;dr

**Part I (the model):** everything is a node in an acyclic
generation DAG. Nodes have kinds (first-class). Edges are typed
references. Manifests anchor versions. Generation = "produce a
node by running its recipe over the nodes it references." That's
it.

**Part II (the implementation):** sparse-replica COW substrate,
wait-free reads, refcount retention with per-kind staggered
curves, sparse load from disk, indexes for ergonomic queries,
markers as labeled manifests, transactions as batched manifest
updates, pruning + resurrection via envelope kinds, routing /
fallback / retry / migration as graph nodes, diagnostics
side-channel.

**MVP boundary:** single-producer-per-node + COW + sparse load +
batched transactions. No CRDT, no OT, no merging beyond disjoint
writes. Multi-instance live edit accommodates without requiring
upfront commitment.

**Concrete next step:** PR-r1 (the AccessPattern ADT). Unchanged
across all five drafts. PR-r3 (the COW node store substrate) is
the largest single PR and where `node_cache.py` + the `8b1c5cd`
surgical fix both go away.

If parts of the framing feel off, flag in chat before any code.
