# REFERENCE-MODEL.md — sparse-replica COW reference tree, excel-shaped processing

**Status:** design proposal, no code yet. Third draft. v1 framed it as
functional dataflow with reachability-based cache lifecycle (committed
as `2cd3233`). v2 (uncommitted, discarded) framed it as a spreadsheet.
Both were wrong in the same direction — over-functional or
over-spatial. v3 distinguishes *structure* (a sparse-replica COW
reference tree, wait-free for readers) from *processing* (excel-shaped
DAG resolution running on top of that structure).

## 1. Premise

Two layers of model, kept rigorously separate:

### Structure: the substrate

The data is stored in a **sparse-replica copy-on-write reference
tree** that's wait-free for examination:

- **Tree** — hierarchical addressing. The address space is meeting →
  strip → position (time-range or ordinal) → cell. Internal nodes are
  tree nodes; leaves are cells.
- **Sparse** — only populated positions take space. Address space is
  unbounded in principle. A strip with one cell every 30 seconds and
  another strip with one cell per meeting cost proportionally
  different amounts.
- **Copy-on-write** — every write produces a new version of the path
  from leaf to root, with structural sharing of the unchanged
  siblings. Old roots remain valid snapshots for in-flight readers.
- **Replica-friendly** — partial replicas can coexist. Different
  RAM-resident working sets in different processes, future
  distributed setups, on-disk subsets that don't shadow the full
  tree — all expressible as roots over the same substrate.
- **Wait-free for examination** — a reader holds a root pointer and
  traverses without locks. Writes never interfere with reads. Atomic
  write = CAS on a root pointer to publish a new version.

This is the same shape Clojure persistent data structures, immer.js,
Git's object database, and ZFS snapshots all use. None of it is
exotic; it's a deliberately conservative choice that gives us
lineage, snapshot consistency, and lock-free reads as a package.

### Processing: excel over the substrate

Cooking is **DAG resolution** over a separate **reference DAG** that
lives orthogonal to the address tree:

- Cells in the tree reference other cells (predecessors). The
  reference relation forms a DAG, validated at graph load to be
  acyclic (acceptable backward-time references; rejected forward
  references — see §4).
- When a predecessor cell's version bumps, the cell that depends on
  it is *dirty*.
- Recook = walk dirty cells in topological order, re-execute their
  recipes, write new cell versions through the substrate.
- This is excel: cells reference cells, formulas re-fire when inputs
  change.

The tree gives us storage + versioning + lineage. The reference DAG
gives us cooking. The two coexist; the tree is what `get(CellId)`
walks; the DAG is what the scheduler walks.

## 2. The cell

A cell is the unit of stored data. Two parts:

```python
# Tree-node metadata. Lives in the tree, not in the user-visible content.
class Cell:
    id: CellId
    version: int                          # monotonic per cell
    writer_node_id: str                   # which node produced this
    recipe: RecipeRef                     # template id + version, optional fork
    inputs: list[VersionedCellId]         # exact cells read, with their versions
    cooked_at_ms: int
    state: Literal["wet", "drying", "dry", "smudged"]
    content: dict                         # the actual data — the node's output dict

class CellId:
    strip: str                            # "strips/turns/diarized"
    time_range: Optional[TimeRange]       # for time-indexed strips
    ordinal:    Optional[int]             # for ordinal-indexed strips

class VersionedCellId:
    cell: CellId
    version: int                          # snapshot of the input at cook time
```

The content **is** the output dict of the node that produced it. Not
a wrapper, not a payload field — the dict directly. Downstream
consumers address into it the same way nodecules edges today connect
`(source_node, output_key) → (target_node, input_key)`: a path into
the dict (`cell.foo.bar[3].baz`) is the natural read.

The metadata (everything except `content`) is carried in the tree
node, not the dict. The dict stays clean — it's just the data,
serializable, no implicit fields. Consumers querying `get(CellId)`
can choose to receive the bare content or the full `Cell` envelope.

The `state` tag expresses pending-ness in-tree, not in a separate
scheduler-state structure. Everything observable about a cell's
lifecycle is on the cell. (`wet` = currently cooking, `drying` =
cooked but downstream not yet refreshed, `dry` = fully settled,
`smudged` = manually marked dirty.)

## 3. The node is the writer

There is no separate "writer registry" or "claim on a strip." A node
in the graph is a (template instance + input mapping) pair, and its
execution produces one output dict per invocation. That dict becomes
the content of one cell at one position.

So:

- Each node has exactly one output → exactly one cell per cooking.
- Single-writer-per-cell falls out of the node shape. No registry
  rejection, no "two templates claiming the same strip" failure mode.
- If two nodes need to fill different views of the same backing
  storage (e.g., L2-turns vs. all-L2-claims into `claims/L2.jsonl`),
  they're two strips with the same backing file and different filters
  — exactly the existing `StripSpec` pattern. Each is a single-writer
  strip.

Practical consequence: the recipe library never needs to enforce
"unique writer." The graph wiring tells you who writes what.

## 4. Reference DAG and typed access

Cells reference predecessor cells via the **typed access patterns**
from v1 §3 (unchanged). A recipe template declares its abstract
access at the library level:

```python
class StripAccess(BaseModel):
    strip_name: str
    pattern: AccessPattern    # ADT: Latest / Range / Before / After /
                              # OrdinalAt / OrdinalRange / SelfRelativeOrdinal

class RecipeTemplate(BaseModel):
    library_id: str
    version: str
    reads:   list[StripAccess]
    writes:  list[str]        # strips this template fills
    op_graph: GraphData

# Time-class and ordinal-class expressions
class TimeExpr:
    SelfWindowStart
    SelfWindowEnd
    SelfWindowStart - Duration
    SelfWindowEnd - Duration
    AbsoluteMs(int)
    # SelfWindowEnd + Duration intentionally absent; forward-time
    # reads are rejected at construction.

class IndexExpr:
    SelfElementIndex
    SelfElementIndex - PositiveInt    # n-1, n-2, ...; legal
    AbsoluteIndex(int)
    # SelfElementIndex + anything intentionally absent.
```

When a node cooks at a specific position, the abstract pattern is
*resolved* against `self.position` → a concrete list of input
`(CellId, version)` pairs. Those pairs are written into the cell's
`inputs` field — the *exact* dependency record, not the abstract
pattern.

This separates two concerns:

- *What the recipe says it reads* (abstract; library-level; static
  cycle analysis runs on this).
- *What this specific cell actually read* (concrete versioned cell
  refs; lineage queries run on this).

A node without declared patterns falls back to the existing runtime
strip API and is unanalyzable by static cycle detection. Migration is
incremental.

### Static acyclicity (extended)

The existing `cycle_validator.py` (PR-n7) rejects coarse "same-strip
read+write" cycles. With typed patterns, it can do finer rejection:
build a `(node, time_class)` graph where `time_class ∈ {past, present,
future}` relative to `self`. Cycles in the `present`-only subgraph
are rejected. Cross-window feedback (`Before(SelfWindowStart)`,
`SelfRelativeOrdinal(n>0)`) resolves to `past` and is always legal.
The "before is fine" escape hatch becomes structural, not idiomatic.

## 5. Recipe library + forking

Recipe templates are versioned and live in a library:

```python
class RecipeRef(BaseModel):
    library_id:      str        # "stenota.summarizer.L2"
    library_version: str        # content hash or semver
    local_fork:      Optional[str] = None  # hash of per-cell overrides; None = library default
```

Cells reference templates via `RecipeRef`. Most cells point at the
library default. A cell can fork its recipe locally for one-off
customization (a per-meeting custom summarizer prompt, an
annotation-attached re-cook directive).

Forking interactions:

- Library version bumps dirty cells pointing at the previous library
  version *iff* `local_fork is None`. Forked cells are insulated.
- Un-forking re-checks against the current library version (might
  dirty the cell).
- A cell with `local_fork = X` and library version `V` is identified
  by `(library_id, V, X)` — different from `(library_id, V', X)` even
  if the fork hash is the same.

The library itself is content-addressable: `library_version` is the
content hash of the template's op-graph + declared inputs/outputs.
"Editing a template" doesn't mutate it; it creates a new version.

## 6. Dirty propagation

A cell is dirty iff any of:

1. **Predecessor changed.** Any `(input_cell, version)` in this cell's
   `inputs` has been superseded — the current version of `input_cell`
   in the tree is higher than the version this cell recorded at cook
   time.
2. **Recipe changed.** `recipe.library_version` differs from the
   library's current version for that `library_id`, AND
   `recipe.local_fork is None`.
3. **Manual mark.** `state == "smudged"` (annotation invalidation,
   tortoise recook, explicit user action).

Recook walks dirty cells in topological order (over the reference
DAG, using each cell's resolved `inputs`), re-executes the recipe
against the current tree state, writes a new cell version through the
substrate. The new cell:

- `version` bumps
- `inputs` records the cells read this time (might differ from prior
  cook if the abstract pattern resolves to different positions now)
- `state` transitions: `wet` while cooking, `drying` after own cook
  but before downstream settles, `dry` when downstream-stable

**Pinned cells.** A cell flagged as pinned ignores predecessor-changed
and recipe-changed dirty signals; only manual smudge / un-pin
triggers recook. Pinning is a per-cell user affordance (the value is
"good as-is, don't touch"). No global pinning policy; no
`is_deterministic` flag at the cache layer. Pinning is the *only*
mechanism for "preserve this output across recooks." LLM outputs the
user reviewed and approved? Pin them. Untouched LLM outputs? They
recook with new seeds; new value, also valid. That's fine.

**`is_deterministic` as a predicate.** Derived from the recipe
template's declared inputs (all-declared = deterministic in the
"would-produce-same-value-from-same-inputs" sense). Used by the
scheduler to skip recooking deterministic cells whose `inputs` haven't
bumped — there's no new value to produce. Not stored on cells; not
used by storage.

## 7. Diagnostics live outside the tree

Errors, logs, traces, perf metrics are observable from outside but
**not** part of the tree. The existing `events.jsonl` event-log is
the right home. Two reasons:

- The tree is *data*. Diagnostics are *about the process that
  produced data*. They have a different audience (developer / user
  trying to debug) and a different lifecycle (often kept longer than
  the data they describe; sometimes scrubbed for privacy).
- Mixing them into the tree muddies the snapshot semantics. A reader
  asking "what is the cell at strips/asr[42]" wants the ASR
  segments; not an error attached to a prior failed cook of an
  upstream cell.

If a cell's recook fails, that's an event in the log + a `state` tag
on the cell (probably a new tag — `failed`? `errored`? — TBD; the
existing four tags don't quite cover this). The error details live in
the event log, not on the cell.

## 8. Retention: RAM and disk both cost money

The tree is unbounded in principle; in practice we have a budget.
Two retention layers:

### RAM (the hot working set)

The substrate supports sparse load: tree internal nodes and leaf
cells can be absent from RAM, fetched from disk on access. The hot
working set is the subset currently resident.

- Loading is lazy: a `get(CellId)` walks the tree; missing subtrees
  trigger a disk fetch.
- Eviction from RAM is *lossless* — drop the in-memory copy, the
  cell still exists on disk, next read reloads. Standard LRU works
  fine here because the only cost is an I/O round-trip.
- Working-set bound is configurable (e.g., `~/.stenota/config.toml`:
  `cache.ram_mb = 256`). Default reasonable for an MBA.

### Disk (durable storage)

Sidecars persist cells. Sidecar storage is *not* unbounded — disk
fills up — so a real retention policy exists:

- **Pin-protected.** Cells the user has pinned never get pruned.
- **Recent.** Cells within the configured retention horizon stay.
  Default: indefinite for v0.x; user-configurable cap later.
- **Project-state.** Cells in the cross-meeting project-state layer
  (`MEMORY.md` → `project_state_layer.md`) have their own retention
  rules (probably indefinite; small in volume).
- **Pruning candidates.** Cells outside all of the above are eligible
  for pruning. Pruning collapses old versions first (keep only
  latest), then collapses dry cells, then collapses settled-and-old
  cells.

Pruning is opportunistic and reversible until commit: it can be
queued, displayed in UI ("about to free 1.2GB; OK?"), and undone
before it lands. Not a silent background sweep.

The crucial property: **storage budget is a user-facing knob**, not
an in-substrate policy that runs on each `put()`. The substrate
accepts new writes; pruning is a separate operation invoked when the
budget is exceeded or the user requests it.

## 9. Lineage

Lineage queries are first-class but opt-in. Default consumer API:

```python
cell_store.get(cell_id)               # latest content
cell_store.get_envelope(cell_id)      # full Cell with metadata
```

Power-user / debugging API:

```python
cell_store.get_at(cell_id, version=V)             # specific version
cell_store.get_lineage(cell_id)                   # full version history
cell_store.walk_inputs(cell_id, depth=N)          # upstream closure
cell_store.walk_impact(cell_id, depth=N)          # downstream closure
```

The closure walks use each cell's `inputs` (upstream) and the
reverse-index from `inputs` (downstream). The reverse-index is built
on demand or maintained as a secondary structure; either way, the
canonical record is the cells' `inputs` lists.

For debugging "what changed because I smudged this annotation?":
`walk_impact(annotation_cell)` returns every cell whose lineage
transitively includes the annotation. The UI affordance is
straightforward.

Version retention vs. cell retention: keeping prior versions of a
cell on disk costs space proportional to the version count. Default
v0.x: keep only the latest cell version on disk; lineage is queryable
within a session (in-memory history) but not durable across sessions.
Opt-in durable history is a v0.5+ feature if the user wants
cross-session "undo" or temporal scrubbing.

## 10. Concurrency

MVP single-writer-per-cell falls out of node shape (§3). What about
parallel cooking?

- **Disjoint strips.** Different nodes filling different strips cook
  concurrently. Each write creates a new root version; the substrate
  merges them via CAS retry or a coalescing root-write loop. No
  conflict possible because the writes touch different leaves.
- **Same strip, different positions.** A windowed cooker filling
  `strips/turns/diarized` at windows W1 and W2 in parallel: each
  write touches a different leaf in the same subtree. The
  COW-with-structural-sharing handles this — they merge by
  combining the two new paths into a common parent. Worst case is a
  CAS retry on the root.
- **Same cell, concurrent writes.** Not reachable in the MVP because
  a cell is written by exactly one node, and the scheduler doesn't
  schedule the same `(node, position)` twice in parallel.
- **User-pin vs. system-recook.** A user pins a cell while the
  scheduler is about to recook it. Pin wins: recook is skipped.
  This is a per-cell policy check (read the pin flag, decide to
  proceed or skip), not a merge problem.

Future concerns (out of scope for v0.x):

- **Multi-instance live edit.** Two stenota processes editing the
  same project. Real OT or CRDT territory. The COW substrate
  accommodates this — each instance holds a root; merges combine
  roots — but the *merge function* needs design. Likely:
  per-strip-class merge policies (LWW for pure-data strips,
  union for annotation strips, manual conflict resolution for
  pinned cells).
- **Redux-shaped action log.** Make the action log canonical; derive
  state from log replay. Compatible with the substrate (versions =
  log positions). Useful if you want "undo to any prior state"
  cheaply. Adds latency to writes.

## 11. What survives from v1

The v1 doc's content-bearing pieces:

- **AccessPattern / TimeExpr / IndexExpr ADT.** Unchanged. Section 4
  here uses it directly.
- **Static cycle detection extension.** The `(node, time_class)`
  graph reasoning carries over verbatim.
- **Structural-determinism predicate.** Still useful, but downgraded
  to "scheduler skip-recook decision" rather than "cache pinning."

The v1 framing pieces that got replaced:

- "Reachability-based cache lifecycle" → became "lineage queries +
  sparse-load on a COW tree." The reachability framing was function-
  centric; the COW-tree framing is data-centric. Same observable
  behavior; different noun structure.
- "Memoization table" → became "the tree IS the data; there's no
  separate memo layer." The off-by-one in `8b1c5cd` becomes
  unrepresentable because there's no shared-state policy loop to
  begin with.

## 12. Smallest viable slice

The PR plan is reordered to put the substrate first.

### PR-r1: AccessPattern ADT (unchanged from v1)

`AccessPattern`, `TimeExpr`, `IndexExpr` in `core/strip_access.py`.
`NodeSpec.reads_strip_patterns: list[StripAccess] = []`. Pydantic
round-trip; static rejection of forward-time / forward-self at
construction. No scheduler, cache, or stenota changes. ~300 lines +
~150 lines of tests.

### PR-r2: cycle_validator extension (unchanged from v1)

Use typed access patterns where declared; coarse same-strip check
where not. ~150 lines + ~250 lines of tests.

### PR-r3: COW cell-store substrate

New module `core/cell_store.py`. Implements the tree as a HAMT
(hash-array-mapped trie) or B-tree-with-structural-sharing variant.
Wait-free reads, CAS atomic writes, sparse load from disk.

- `CellStore` interface: `get(cell_id) → Optional[Cell]`,
  `put(cell, parent_root) → new_root`, `get_at(cell_id, version)`,
  `get_lineage(cell_id) → list[Cell]`.
- `InMemoryCellStore` (HAMT-backed).
- `FilesystemCellStore` (disk-backed; tree blocks stored as content-
  addressable files; root pointer file CAS-rotated).
- The existing `node_cache.py` (and the surgical fix in `8b1c5cd`)
  gets removed in this PR. The whole module goes away.
- ~1200 lines + ~800 lines of tests. The biggest single PR; the
  substrate is load-bearing.

### PR-r4: dirty propagation + cell metadata

- Cell `state` tags, `version`, `inputs`, `recipe`, `writer_node_id`
  wired through the cooker.
- Scheduler tick walks dirty set, recooks in topological order, calls
  `cell_store.put` for each result.
- `is_deterministic` derived predicate; used to skip recooks of
  deterministic cells whose `inputs` haven't bumped.

### PR-r5: recipe library + forking

`RecipeTemplate`, `RecipeLibrary`, `RecipeRef`. Library version
bumps; per-cell forking. UI / API surface for "pin this cell."

### PR-r6: retention policy

RAM working-set bound (LRU on sparse tree subtrees). Disk pruning
policy (recent / pinned / project-state preserved; everything else
eligible). User-facing knob in config.

### PR-r7: diagnostics side-channel

If `events.jsonl` isn't sufficient, formalize the diagnostics stream.
Probably already enough; this is a placeholder PR for "make sure
errors and logs have a clean home outside the tree."

### PR-r8: stenota migration

Stenota nodes declare access patterns, write through the cell store,
consume from it. The existing `claims/L2.jsonl` / `claims/L3A.jsonl`
sidecar files become serialized cell-store output (same on-disk
format if we keep JSONL as the tree-leaf representation, or a new
format if HAMT serialization is more efficient).

## 13. Relationship to deferred PRs

- **PR-n7b (scheduler-v2).** Hare/tortoise = two cookers running
  against the same tree. With COW substrate this is structurally
  clean: each holds its own root, writes produce new versions, the
  tree merges them via CAS. Dirty-queue = the recipe-driven
  computation of which `(cell)` pairs need recooking. PR-r3 + PR-r4
  give it the substrate it needs.
- **PR-n9b (concrete LLM adapters).** Orthogonal. Not blocked.
- **PR-s2b (stenota ctx.strip migration).** Subsumed by PR-r8.

## 14. Hard invariants (in addition to existing)

- **Cells are the unit of persistence.** The cell store is the source
  of truth. Sidecars are the cell store's on-disk representation.
- **Writes produce new versions.** No in-place cell mutation. Each
  write through `cell_store.put` produces a new tree root.
- **Reads are wait-free.** `cell_store.get(cell_id)` never blocks on a
  writer. Reads from an in-flight cook see the pre-cook value;
  reads after the cook completes see the new value.
- **Single writer per cell.** Falls out of the graph wiring; not
  enforced at the registry layer.
- **`inputs` records exact versions.** Replaying a cook with the
  same `inputs` should produce the same result for deterministic
  recipes. For nondeterministic recipes, the replay is "what would
  the recipe produce now from these inputs" — a valid answer, not
  necessarily byte-identical.
- **Diagnostics live outside the tree.** Errors, logs, traces go to
  the event log; the tree stays clean.
- **The sidecar is bounded in practice.** Disk retention is a
  user-facing policy. The substrate accepts writes; pruning is a
  separate operation.
- **Cell `state` tags are observable.** `wet`/`drying`/`dry`/
  `smudged` (and possibly `failed`) live on the cell, queryable.
  This is the lifecycle state machine — there's no other.

## 15. Open questions

- **HAMT vs. B-tree for the substrate.** HAMT is simpler to reason
  about and has well-known wait-free read properties; B-tree might
  pack denser for time-ranged strips with regular cadence. Probably
  HAMT for v0.x; revisit if profiling justifies the complexity.
- **On-disk tree representation.** Content-addressable blocks (Git-
  style) or a single LSM-tree-like log? Content-addressable composes
  well with distributed replicas; LSM has better write throughput.
  Likely content-addressable for v0.x.
- **Failed cooks.** Add a `failed` cell state? Or stay with `smudged`
  + an event-log error entry? Probably `failed` as a fifth tag, with
  an `error_ref` pointing into the event log for details.
- **`writer_node_id` portability.** If a graph is edited (node deleted,
  re-added with a new id), cells written by the old node still
  reference the old id. Probably fine — `writer_node_id` is a
  historical fact, not a current pointer. UI may surface "this
  cell's writer no longer exists in the current graph."
- **Cross-meeting project-state cells.** The project-state layer
  (`persons/`, `lenses/`, `templates/`) needs cells too, but
  scoped above any one meeting. `CellId` gains an optional
  `scope: Literal["meeting", "project"]`? Or a separate root in the
  tree? Probably the latter.
- **Substrate concurrency model.** Single-process MVP: CAS on root
  pointer is enough. Multi-process: the root pointer becomes a
  filesystem-level resource that needs locking or a coordinator.
  Out of scope until multi-process is a real requirement.

## 16. Connections to existing MEMORY entries

- **`abstraction_discipline.md`** — "no `if provider == ...` in core."
  Same principle: no `if is_deterministic` flag in storage; structural
  property derived from declarations.
- **`structural_over_policy.md`** — flag-based policy in loops on
  shared state is a design smell. The COW substrate makes
  flag-discriminated eviction unrepresentable.
- **`mutability_is_figurative.md`** — wet/drying/dry/smudged as UI
  tag, not state machine. Reinforced: tags live on cells, observable,
  but there's no separate scheduler-state structure.
- **`annealing_not_constraints.md`** — labels are observations, not
  constraints. Reinforced: pin flags are user-affordances on cells,
  not registry-level policy.
- **`signals_and_slots.md`** — subscription layer tailing the event
  log. Compatible: subscriptions watch the event log (which is where
  diagnostics live), separate from the cell store (which is where
  data lives).
- **`chunk_size_principle.md`** — chunk size is tractability, not
  granularity. Compatible: cell granularity is whatever a recipe
  outputs; the substrate doesn't care.

## 17. tl;dr

**Structure:** sparse-replica COW reference tree, wait-free reads,
versioned writes. The tree is the storage; nothing else is.

**Processing:** excel DAG over the tree. Cells reference predecessor
cells via typed access patterns. Recook walks dirty cells in
topological order; each recook writes a new cell version through the
substrate.

**Writer identity is the node.** Each node emits one output dict per
cook; that dict becomes one cell's content. Single-writer-per-cell
falls out structurally, no registry needed.

**Diagnostics live outside the tree.** Event log for errors / logs /
traces. The tree stays clean: data, or data-pending-with-tags.

**Retention is two-tier.** RAM = sparse working set, lossless LRU.
Disk = real budget with user-facing policy (recent / pinned /
project-state preserved; rest eligible for pruning).

**Lineage is queryable.** Each cell records exact input versions;
walk upstream / downstream for "what depends on this?" / "what
produced this?". Default API returns latest; lineage is opt-in.

**MVP boundary:** single-writer-per-cell + COW + sparse load. No
CRDT, no OT, no merging beyond disjoint-write CAS. Multi-instance
live edit is a future feature; the substrate accommodates it without
preempting design space.

Concrete next step: PR-r1 (the AccessPattern ADT, declarative-only).
Unchanged from v1. PR-r3 (the COW cell store) is the biggest single
PR and where `node_cache.py` + the `8b1c5cd` surgical fix both
disappear.

If parts of the framing feel off, flag in chat before any code.
