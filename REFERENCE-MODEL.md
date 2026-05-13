# REFERENCE-MODEL.md — sparse-replica COW reference tree, excel-shaped processing

**Status:** design proposal, no code yet. Fourth draft. v1 framed it
as functional dataflow with reachability cache (committed as
`2cd3233`). v2 (uncommitted, discarded) framed it as a spreadsheet.
v3 settled on COW reference tree + excel DAG (committed as `cd3367f`).
v4 incorporates several chat-driven refinements: first-class
envelope/content split, transactions from batched root writes,
multi-axis markers, refcount-rules-everything for retention,
per-strip retention policies with staggered curves, external leaves
+ resurrection unifying recook/cold-start/rebuild, and the
proxy-edit substitution pattern for live cooks.

The two-layer framing (structure vs. processing) is unchanged from
v3; everything below is additive refinement.

## 1. Premise

Two layers, kept rigorously separate:

### Structure: the substrate

The data is stored in a **sparse-replica copy-on-write reference
tree**, wait-free for examination:

- **Tree** — hierarchical addressing: meeting → strip → position →
  cell. Internal nodes are tree nodes; leaves are cells.
- **Sparse** — only populated positions take space. Address space
  is unbounded in principle; populated cells are bounded by what's
  cooked.
- **Copy-on-write** — every write produces a new version of the
  path from leaf to root, with structural sharing of unchanged
  siblings. Old roots remain valid snapshots for in-flight readers.
- **Replica-friendly** — partial replicas coexist. Different
  RAM-resident working sets, future distributed setups, on-disk
  subsets — all expressible as roots over the same substrate.
- **Wait-free for examination** — a reader holds a root pointer and
  traverses without locks. Writes never interfere with reads.
  Atomic write = CAS on a root pointer.

Same shape as Clojure persistent data structures, immer.js, Git's
object database, ZFS snapshots. None of this is exotic; it's a
deliberately conservative choice that gives lineage, snapshot
consistency, lock-free reads, and natural transactions as a package.

### Processing: excel over the substrate

Cooking is **DAG resolution** over a separate **reference DAG**
that lives orthogonal to the address tree:

- Cells in the tree reference predecessor cells. The reference
  relation forms a DAG, validated acyclic at graph load.
- When a predecessor's version bumps, dependent cells are *dirty*.
- Recook = walk dirty cells in topological order; re-execute the
  recipe against current state; write new cell versions through
  the substrate.

The tree gives storage + versioning + lineage + transactions. The
reference DAG gives cooking. The two coexist; the tree is what
`get(...)` walks; the DAG is what the scheduler walks.

## 2. The cell — envelope + content, first-class

Every cell has two parts, **independently addressable, independently
refcounted, independently retainable**:

- **Envelope** — cheap (~hundreds of bytes). The metadata that
  describes the cell and lets it be rebuilt: id, version, writer,
  recipe, exact-versioned inputs, cook time, state tag.
- **Content** — expensive (bytes to megabytes). The actual data
  dict the recipe produced. The output of the node's execution.

```python
class CellId(BaseModel):
    strip: str
    time_range: Optional[TimeRange] = None
    ordinal:    Optional[int] = None

class Envelope(BaseModel):
    id: CellId
    version: int                          # monotonic per cell
    writer_node_id: str
    recipe: RecipeRef
    inputs: list[VersionedCellId]         # exact cells read, with their versions
    leaves: list[ExternalLeafRef]         # external resources read at cook time (§7)
    cooked_at_ms: int
    cooked_from: dict[str, ResourceRef]   # which substitute was used per leaf (§7)
    state: Literal["wet", "drying", "dry", "smudged", "failed"]

class Content(BaseModel):
    id: CellId
    version: int
    data: dict                            # the node's output dict
```

Same `cell_id` addresses both. Two access paths at the cell-store
API:

```python
cell_store.get_envelope(cell_id) -> Optional[Envelope]
cell_store.get_content(cell_id) -> Optional[Content] | Absent | Lost
```

Reasons for the split:

- **Independent refcounts.** A retention policy can drop content
  while keeping envelopes (the resurrection contract — §8). User
  / annotation / in-flight reader refs on either side keep that
  side alive regardless of policy.
- **Cheap tombstones.** A pruned cell with envelope still present
  is honest about history without paying the content cost.
- **Lineage queries don't pull content.** Walking impact ("what
  depends on cell X") only needs envelopes. Content fetches are
  on demand.

The `state` tag is **on the envelope**, not in a separate scheduler
structure. The lifecycle (`wet` / `drying` / `dry` / `smudged` /
`failed`) is observable in-tree; there's no other state machine.
(`failed` is new in v4 — explicit "this cell's last cook errored;
see event log for details." Sits cleanly alongside the four UI tags
in `MEMORY.md`.)

## 3. The node is the writer

No separate "writer registry" or strip claim. A node = (template
instance + input mapping). Execution produces one output dict per
invocation; that dict becomes one cell's content. Single-writer-
per-cell falls out of node shape; no registry-level enforcement
needed.

If two views of the same backing storage exist (e.g., L2-turns and
all-L2-claims into `claims/L2.jsonl`), they're two strips with the
same backing file and different filters — exactly the existing
`StripSpec` pattern. Each is a single-writer strip.

## 4. Reference DAG and typed access

Cells reference predecessor cells via the **typed access patterns**
from v1 §3 (unchanged):

```python
class AccessPattern:
    Latest
    Range(start: TimeExpr, end: TimeExpr)
    Before(at: TimeExpr) / After(at: TimeExpr)
    OrdinalAt(index: IndexExpr) / OrdinalRange(start, end: IndexExpr)
    SelfRelativeOrdinal(offset: PositiveInt)

class TimeExpr:
    SelfWindowStart / SelfWindowEnd
    SelfWindowStart - Duration / SelfWindowEnd - Duration
    AbsoluteMs(int)
    # SelfWindowEnd + Duration intentionally absent (forward-time rejected)

class IndexExpr:
    SelfElementIndex
    SelfElementIndex - PositiveInt
    AbsoluteIndex(int)
    # SelfElementIndex + anything intentionally absent (forward-self rejected)
```

A recipe template declares its abstract `reads`. When a node cooks
at a specific position, the abstract pattern is *resolved* against
`self.position` → a concrete list of input `(CellId, version)`
pairs, written into `envelope.inputs`. Two concerns separate:

- *What the recipe says it reads* (abstract; library-level; static
  cycle analysis runs on this).
- *What this specific cell actually read* (concrete versioned cell
  refs; lineage queries run on this).

A node without declared patterns falls back to the existing runtime
strip API, unanalyzable by static cycle detection. Migration is
incremental.

### Static acyclicity

The existing `cycle_validator.py` rejects coarse same-strip cycles.
With typed patterns it does finer rejection: build a `(node,
time_class)` graph where `time_class ∈ {past, present, future}`
relative to `self`. Cycles in the `present`-only subgraph are
rejected. Cross-window feedback (`Before(SelfWindowStart)`,
`SelfRelativeOrdinal(n>0)`) resolves to `past` and is always legal.

## 5. Recipe library + forking

Templates are versioned and library-resident:

```python
class RecipeRef(BaseModel):
    library_id:      str        # "stenota.summarizer.L2"
    library_version: str        # content hash
    local_fork:      Optional[str] = None  # hash of per-cell overrides
```

- Library version bumps dirty cells pointing at the previous version
  iff `local_fork is None` (forked cells are insulated).
- Un-forking re-checks against the current library version.
- `library_version` is the content hash of the template; "editing"
  produces a new version, never mutates.

## 6. External leaves and source-data references

The data graph terminates at **external leaves** — references to
durable storage outside the cell store. Media files, sensor streams,
recorded HTTP responses, anything the cell store doesn't manage.

```python
class ResourceRef(BaseModel):
    scheme:  str                # "media", "live", "sensor", "http"
    locator: str                # "meeting_abc.mp4", "mic:0", "https://..."
    slice:   Optional[SliceSpec] = None   # byte range, time range, etc.

class ExternalLeafRef(BaseModel):
    primary:     ResourceRef
    substitutes: list[ResourceRef] = []   # ordered fallbacks
```

A cell's envelope carries `leaves: list[ExternalLeafRef]` for the
external resources its cook touched. The cell-store doesn't hold
the bytes; a `ResourceHandler` plugin keyed by scheme knows how to
fetch them when needed.

### Proxy-edit substitution: the live-cook pattern

Live mode reads from `live://mic:0#0..30s` while simultaneously
recording to `media://meeting.mp4#audio[0..30s]`. At cook time the
live ref resolves; the recording isn't necessarily complete yet.
After the meeting ends, the live ref is gone; the recording is the
durable source.

The pattern:

- Recipe declares: `leaves = [ExternalLeafRef(primary=live://...,
  substitutes=[media://...])]`
- At cook time: try `primary`; if it resolves, use it; record
  `cooked_from[leaf_id] = primary`.
- If primary fails, walk substitutes in order; record whichever
  worked.
- At rebuild time: same dance. If `cooked_from` was `primary` and
  primary no longer resolves, the substitute is now the source.
  Rebuild outcome annotated (see §8).

Direct analogue: video proxy editing. Cut against the proxy in
realtime, conform against the master at finalization. The cell
store knows it's working with a proxy; the substitution is honest
and traceable.

## 7. Resurrection unifies recook, cold-start, and rebuild

Three operations collapse into one: **given an envelope, produce
content.**

- **Dirty recook** — content exists but is stale. Discard, rebuild.
- **GC'd resurrection** — content was pruned by retention policy.
  Rebuild from envelope's recipe + inputs + leaves.
- **Cold start** — cell never cooked (envelope exists, content
  never produced). Rebuild = first cook.

All three: walk `envelope.inputs` (recursively resurrect as needed),
resolve `envelope.leaves` (resolve `primary` or fall back to a
`substitute`), execute the recipe, write new content. Same
machinery; only the trigger differs (dirty signal, GC sweep,
scheduler tick).

### Four resurrection outcomes

A `get_content(cell_id)` call that triggers rebuild has four
possible outcomes:

1. **Recoverable / exact** — every transitive dependency resolves,
   recipe is deterministic, no substitutes used. Rebuilt content is
   byte-identical to original.
2. **Recoverable via substitute** — a substitute was used in place
   of an unresolvable primary leaf somewhere in the chain. For
   pure-audio strips this is usually fine (the recording is
   byte-identical to what came in live); for VLM frame-grabs it
   might differ (keyframe alignment). UI flags it.
3. **Equivalent-but-not-identical** — recipe is nondeterministic
   (unfixed-seed LLM, randomness) OR recipe library version moved
   on. Rebuilt content is *a* valid value; not the original. UI
   surfaces it as "rebuilt with new parameters; pin if you wanted
   the prior value."
4. **Lost forever** — some envelope in the chain is gone OR an
   external leaf is unresolvable (primary AND all substitutes fail).
   Returns a `Lost` sentinel with the reason.

A `Lost` cell's envelope can still be queried. The envelope is the
tombstone: it says "I remember a cell was here; this is what it
was for; here's why rebuilding failed." UI affordances: delete the
envelope (and cascade to dependents), annotate as historical-only,
or attempt rebuild after the user provides a new substitute (e.g.,
pointing the system at a re-located media file).

## 8. Dirty propagation

A cell is dirty iff any of:

1. **Predecessor changed.** Some `(input_cell, version)` in
   `envelope.inputs` has been superseded.
2. **Recipe changed.** `recipe.library_version` differs from the
   library's current version AND `recipe.local_fork is None`.
3. **Manual mark.** `state == "smudged"`.

Recook walks dirty cells in topological order, treating each recook
as a resurrection (§7). The same operation handles cold start
(envelope exists, no content), GC'd content (envelope exists,
content was pruned), and stale content (envelope + content, both
present, but inputs moved on).

**Pinning.** A cell's envelope can carry a `pinned: bool` flag. A
pinned cell ignores predecessor-changed and recipe-changed dirty
signals; only manual smudge / un-pin triggers recook. Pinning is the
*only* mechanism for "preserve this output across recooks." LLM
outputs the user reviewed and approved? Pin them. Untouched LLM
outputs? They recook with new seeds — a new valid value, fine.

**`is_deterministic` is a predicate, not a flag.** Derived from the
recipe template's declared inputs (all-declared + no external-effect
tags = deterministic). Used by the scheduler to skip recooking
deterministic cells whose `inputs` haven't bumped (no new value to
produce). Not stored on cells; not used by storage.

## 9. Diagnostics live outside the tree

Errors, logs, traces, perf metrics live in the event log
(`events.jsonl`), not in the tree. The tree is *data*; diagnostics
are *about the process that produced data*. Different audience,
different lifecycle (often longer-lived than the data they
describe; sometimes scrubbed for privacy).

A failed cook produces an event-log entry plus a `failed` envelope
state tag. The error details live in the event log, not on the cell.
Subscription consumers (`MEMORY.md` → `signals_and_slots.md`) tail
the event log; they're independent of cell-store operations.

## 10. Retention: refcount is the mechanism

**Refcount rules everything.** Retention "policy" is just *when the
system releases its own refs*. User refs, annotation refs, in-flight
reader handles, root pointers — any of them keep a cell alive
regardless of policy. The system is one ref-holder among many.

Two-tier retention with envelope/content split:

### RAM (hot working set)

- Lazy load: tree internal nodes + leaf cells absent from RAM,
  fetched on read. Sparse traversal from root.
- Eviction: drop in-memory copy when refcount → 0. *Lossless* — the
  next read reloads from disk. Standard LRU on system-held refs
  works fine.
- Bound: configurable (e.g., `~/.stenota/config.toml`:
  `cache.ram_mb = 256`). Default reasonable for an MBA.

### Disk (durable storage)

- The cell store is the on-disk representation; sidecars persist
  cells. Per-cell pruning is real because disk fills up.
- Pruning *can* be lossy (no recovery without rebuild) but
  envelope-content split keeps the loss small: prune content
  aggressively, keep envelope (the resurrection slate) cheap.

### Staggered, per-strip retention

Different strips have wildly different cost/value:

| Strip                          | Content retention                          | Envelope retention   |
|--------------------------------|--------------------------------------------|---------------------|
| `strips/raw/audio`             | latest only; rebuild from media file       | always (cheap)      |
| `strips/asr/segments`          | last hour + markers; daily checkpoints     | always              |
| `strips/diar/segments`         | last hour + markers; daily checkpoints     | always              |
| `strips/claims/L2`             | every version (small; expensive to recook) | always              |
| `strips/claims/L4`             | every version (tiny; LLM unfixed seed)     | always              |
| `strips/annotations/*`         | every version forever                      | always              |
| `strips/raw/video` (future)    | latest only; rebuild from media file       | always              |

Each strip's retention rule is "the system holds its own ref to
content at markers M_C, and to envelopes at markers M_E, with M_E ⊇
M_C." User refs override; pinned cells override.

### Staggered curves

Retention density follows a staggered curve, not a single cliff:

```
density of retained content at marker offset from now
  ┌────────
  │      ╲___
  │          ╲___
  │              ╲___
  │                  ╲___
  └──────────────────────────► marker offset
  now            1h          1d        1mo
```

Last hour: every marker preserved. Last day: every 10 minutes.
Last month: daily. Older: weekly. Configurable per strip. Free with
COW because structural sharing means N retained markers share most
of their bytes.

### Lost-forever is a real outcome

Even with envelope retention, content can be unrecoverable: the
media file went missing, the live stream wasn't recorded, the
recipe library no longer has the version that cooked the cell. The
`Lost` sentinel is honest about this. The envelope sticks around as
a tombstone (until nothing references it), and the UI can offer to
delete it.

## 11. Markers and transactions

### Markers generalize beyond time

A marker is a **labeled root pointer**. Labels can be:

- transaction id (every batched commit)
- semantic event ("ASR window 12 settled", "user accepted speaker
  name proposal")
- user action ("undo point auto-set before destructive edit")
- recipe-library version bump
- wall-clock checkpoint ("save every N seconds")

Multiple labelings coexist on the same root. The history is
navigable along any axis: "show me the last 5 transactions," "show
me what changed when annotation X was added," "go back to before
the 4:32pm library bump."

### Transactions = batched root writes

The COW substrate gives ACID for free when writes are batched:

- Collect N cell puts against the current root (structural sharing
  means construction against the old root is cheap).
- CAS the root once to publish all N atomically.

Properties:

- **Atomic** — root pointer moves or doesn't. Either every cell in
  the batch is visible or none is.
- **Consistent** — the new tree was built against a single prior
  snapshot. No torn reads.
- **Isolated** — readers on the old root see the old world until
  the CAS lands; readers on the new root see the new world. Wait-
  free for both.
- **Durable** — persist the new root before CAS.

Datomic, Clojure's `swap!`, and many functional databases work
exactly this way. The "nearly for free" caveat: same-cell concurrent
writes still need conflict resolution, but the MVP's single-writer-
per-cell rule (§3) makes those structurally absent.

A scheduler tick is naturally a transaction: cook everything dirty,
batch all the new cells, CAS the root once. Failures roll back by
discarding the in-progress root (just drop the local construction
work; the published root never moved).

## 12. Lineage

First-class queryable history. Default API returns latest content
or envelope; lineage is opt-in:

```python
cell_store.get_envelope(cell_id)               # latest envelope
cell_store.get_content(cell_id)                # latest content (may return Absent / Lost)
cell_store.get_at(cell_id, version=V)          # specific version
cell_store.get_at_marker(cell_id, marker=M)    # version as of marker M
cell_store.get_lineage(cell_id)                # version history (envelope + content where retained)
cell_store.walk_inputs(cell_id, depth=N)       # upstream closure
cell_store.walk_impact(cell_id, depth=N)       # downstream closure
```

`walk_impact` uses the inverse-index from `envelope.inputs` —
"every cell whose `inputs` contains this cell." Built on demand or
maintained as a secondary structure. The canonical record is the
forward `inputs`; the inverse is derived.

Annotation impact debugging — "what changed because I smudged this
annotation?" — is just `walk_impact(annotation_cell)`. UI surfaces
the result as a list of cells with their version transitions.

## 13. Concurrency

MVP scope: single-process, single-writer-per-cell, batched
transactions on the root.

- **Disjoint strips.** Different nodes filling different strips
  cook concurrently; writes touch disjoint leaves; CAS retries
  resolve trivially.
- **Same strip, different positions.** Parallel windowed cooks
  touch different leaves in the same subtree; COW structural
  sharing handles this; worst case is a CAS retry.
- **Same cell, concurrent writes.** Structurally absent (single-
  writer rule).
- **User pin vs system recook.** Per-cell policy (pin wins),
  not a merge.

Future (out of scope for v0.x):

- **Multi-instance live edit.** Two stenota processes editing the
  same project. Real OT or CRDT territory; merge functions
  per-strip-class. Substrate accommodates; designs deferred.
- **Redux-shaped action log.** Make the event log canonical, derive
  state from log replay. Compatible with the substrate (versions =
  log positions); adds write latency.

## 14. What survives from earlier drafts

From v1 / v3:

- **AccessPattern / TimeExpr / IndexExpr ADT** — unchanged. §4
  uses it directly.
- **Static cycle detection extension** — unchanged.
- **Structural-determinism predicate** — unchanged.
- **COW reference tree + wait-free reads + structural sharing** —
  unchanged from v3.
- **Excel DAG processing on top of the tree** — unchanged from v3.
- **Node-as-writer** — unchanged from v3.
- **Diagnostics outside the tree** — unchanged from v3.

New in v4:

- **Envelope/content split as first-class** — §2.
- **External leaves with primary + substitute refs** — §6.
- **Resurrection unifies recook / cold-start / rebuild** — §7.
- **Four-outcome resurrection** (exact / via-substitute /
  equivalent / lost) — §7.
- **`failed` cell state tag** — §9.
- **Refcount-rules-everything for retention** — §10.
- **Staggered per-strip retention curves** — §10.
- **Markers as labeled root pointers** — §11.
- **Transactions as batched root writes** — §11.
- **`get_at_marker` lineage query** — §12.

## 15. Smallest viable slice

The PR plan from v3, lightly revised:

### PR-r1: AccessPattern ADT (unchanged)

`AccessPattern`, `TimeExpr`, `IndexExpr` in `core/strip_access.py`.
`NodeSpec.reads_strip_patterns: list[StripAccess] = []`. Pydantic
round-trip; static rejection at construction. No scheduler / cache /
stenota changes. ~300 lines + ~150 lines of tests.

### PR-r2: cycle_validator extension (unchanged)

Use typed access patterns where declared; coarse same-strip check
where not.

### PR-r3: COW cell-store substrate

`core/cell_store.py` — HAMT or B-tree-with-structural-sharing.
Wait-free reads, CAS atomic writes, sparse load from disk.
**Envelope and content are first-class separate addresses** with
independent refcounts.

- `CellStore` interface: `get_envelope`, `get_content`, `put`
  (transaction-batched), `get_at`, `get_at_marker`, `get_lineage`,
  `walk_impact`.
- `InMemoryCellStore` (HAMT-backed, refcount-driven retention).
- `FilesystemCellStore` (disk-backed; content-addressable blocks;
  root pointer file CAS-rotated).
- `node_cache.py` + the `8b1c5cd` surgical fix both removed.
- ~1500 lines + ~1000 lines of tests. Largest single PR; the
  substrate is load-bearing.

### PR-r4: dirty propagation + resurrection

- Envelope state tags wired through the cooker.
- Scheduler walks dirty set, recooks in topological order via the
  unified resurrection operation, batches puts into a transaction.
- `is_deterministic` predicate; skip recooks for deterministic
  cells whose `inputs` haven't bumped.
- `failed` state path: event-log entry on cook failure; envelope
  carries the tag with a ref into the event log.

### PR-r5: recipe library + forking

`RecipeTemplate`, `RecipeLibrary`, `RecipeRef`. Library version
bumps; per-cell forking. UI / API surface for "pin this cell."

### PR-r6: external leaves + ResourceHandler plugins

- `ExternalLeafRef` + `ResourceRef` types.
- `ResourceHandler` plugin interface; per-scheme implementations
  (`media://` for files, `live://` for streams, `sensor://` for
  realtime sensor data, etc.).
- Primary + substitute resolution with `cooked_from` annotation.
- Tests cover the live-cook → record-substitution case.

### PR-r7: retention policy

- Per-strip retention rules (in strip registration metadata).
- Staggered curves implemented as marker-keyed system refs.
- RAM working-set bound (LRU on system-held refs).
- Disk pruning (refcount = 0 → drop; envelope-content split
  honored).
- User-facing knobs in config.

### PR-r8: diagnostics side-channel

If `events.jsonl` isn't sufficient, formalize it. Likely just
documentation + a small wrapper.

### PR-r9: stenota migration

Stenota nodes declare access patterns, write through the cell
store, consume from it. Existing `claims/L2.jsonl` etc. become
cell-store-managed.

## 16. Relationship to deferred PRs

- **PR-n7b (scheduler-v2).** Hare/tortoise = two cookers on the
  same tree, each holding its own root, writes producing new
  versions, tree merging via CAS. PR-r3 + PR-r4 give it the
  substrate.
- **PR-n9b (concrete LLM adapters).** Orthogonal. Not blocked.
- **PR-s2b (stenota ctx.strip migration).** Subsumed by PR-r9.

## 17. Hard invariants

- **Cells are the unit of persistence.** Cell store is the source
  of truth; sidecars are its on-disk representation.
- **Writes produce new versions.** No in-place mutation. Each put
  produces a new tree root (within a transaction batch).
- **Reads are wait-free.** Never block on writers.
- **Single writer per cell.** Falls out of graph wiring.
- **`inputs` records exact versions.** Resurrection is deterministic
  for deterministic recipes given the same input versions and same
  recipe library version.
- **Envelopes and content are independently refcounted.** Retention
  applies independently at each level.
- **Diagnostics live outside the tree.** Event log; not on cells.
- **Refcount rules retention.** Policy = when the *system* releases
  its own refs. User / annotation / in-flight refs override.
- **External leaves can substitute.** Primary + substitutes
  ordered; `cooked_from` annotates which was used. Lost-forever
  is a real outcome; it's surfaced as `Lost`, not silently masked.
- **Markers are labeled roots.** Multi-axis history navigation
  (time / transaction / semantic event / user action / recipe-
  version).
- **Transactions are batched root writes.** ACID falls out of CAS
  on a single root pointer per commit.

## 18. Open questions

- **HAMT vs B-tree.** HAMT for simplicity / wait-free read
  properties; revisit if profiling justifies.
- **On-disk tree representation.** Content-addressable blocks
  (Git-style) for v0.x; LSM if write throughput dominates later.
- **Failed cooks.** Confirmed as a `failed` state tag with error
  refs in the event log. Open: do we retry automatically, or
  require explicit user action? Probably: retry once with
  exponential backoff for transient failures (network errors),
  require user action for persistent failures.
- **`writer_node_id` portability.** When a graph is edited (node
  re-id'd or deleted), old cells still reference the old id. Treat
  as historical fact, not a current pointer. UI may surface "this
  cell's writer no longer exists in the current graph."
- **Cross-meeting project-state cells.** L5-tier state (`persons/`,
  `lenses/`, `templates/`) needs scoping. Probably: `CellId` gains
  `scope: Literal["meeting", "project"]`, project cells live in a
  separate root with cross-meeting roots referencing them.
- **Substrate concurrency.** Single-process: CAS on root pointer
  is enough. Multi-process: filesystem-level lock or coordinator
  process. Out of scope until multi-process is a real requirement.
- **Marker GC.** When are markers themselves prunable? If marker
  M_old has no labeled queries and no system-policy holds it,
  drop it. Refcount applies to markers the same way it applies to
  cells.
- **Substitute discovery for already-cooked cells.** If a cell was
  cooked live-only and no substitute was recorded then, can the
  user later attach a substitute (e.g., "I have the recording now,
  please use it for rebuilds")? Probably yes via an envelope-level
  annotation, but the mechanics need design.

## 19. Connections to MEMORY entries

- **`abstraction_discipline.md`** — no provider-name branching in
  core. Same principle: no `is_deterministic` flag in storage;
  structural property derived from declarations.
- **`structural_over_policy.md`** — flag-based policy in shared-
  state loops is a design smell. COW substrate makes
  flag-discriminated eviction unrepresentable.
- **`mutability_is_figurative.md`** — wet/drying/dry/smudged as UI
  tag, not state machine. Reinforced: tags live on envelopes,
  observable, no separate scheduler-state structure.
- **`annealing_not_constraints.md`** — labels are observations,
  not constraints. Reinforced: pin flags are user affordances on
  cells, not registry-level policy.
- **`signals_and_slots.md`** — subscription layer tailing event
  log. Compatible: subscriptions watch the event log; separate
  from the cell store.
- **`chunk_size_principle.md`** — chunk size is tractability, not
  granularity. Compatible: cell granularity is whatever a recipe
  outputs.

## 20. tl;dr

**Structure:** sparse-replica COW reference tree, wait-free reads,
versioned writes. Cells split into envelope (cheap, the
resurrection slate) and content (expensive, the actual data),
independently addressable and independently refcounted.

**Processing:** excel DAG over the tree. Cells reference
predecessors via typed access patterns. Recook walks dirty cells
in topological order; each recook is a resurrection (recipe
applied to `envelope.inputs` + `envelope.leaves`).

**Resurrection unifies** recook / cold-start / rebuild. Four
outcomes: exact / via-substitute / equivalent / lost. External
leaves with primary + substitute resource refs enable proxy-edit
patterns (live cook, durable rebuild).

**Writer identity is the node.** Single-writer-per-cell falls out
of graph wiring; no registry rejection needed.

**Diagnostics live outside the tree.** Event log for errors / logs
/ traces. Tree stays clean: data, or data-pending-with-tags.

**Retention is refcount-driven.** Per-strip, per-envelope-vs-
content, with staggered density curves. User / annotation / in-
flight refs always override policy. Pruning is just the system
releasing its own refs.

**Markers generalize beyond time** (labeled root pointers; multi-
axis history navigation). **Transactions** fall out of batched
root writes (ACID on a single CAS).

**MVP boundary:** single-writer-per-cell + COW + sparse load +
batched transactions. No CRDT, no OT, no merging beyond disjoint
writes. Multi-instance live edit accommodates without requiring
upfront commitment.

Concrete next step: PR-r1 (the AccessPattern ADT). Unchanged across
drafts. PR-r3 (the COW cell store with envelope/content split) is
the largest PR and where `node_cache.py` + the `8b1c5cd` surgical
fix both go away.

If parts of the framing feel off, flag in chat before any code.
