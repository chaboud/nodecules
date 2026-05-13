# REFERENCE-MODEL.md — typed access patterns + reachability-based cache lifecycle

**Status:** design proposal, no code yet. Drafted during the same
verification pass that produced `HANDOFF-RESPONSE.md`. Reacts to two
specific concerns surfaced in chat:

1. The cache eviction off-by-one in `8b1c5cd` is a symptom of treating
   storage policy as imperative phased loops over a flag-discriminated
   store, rather than as a property of the surrounding model.
2. Access into strips is inherently relational (`ParentStrip[me - 1]`,
   `OtherStrip[time_range]`, `RefStrip[seek(t) - 5 .. seek(t)]`) and
   the system can only reason about acyclicity / reachability /
   replayability if those relations are declared as typed expressions
   rather than executed as opaque runtime calls.

Both concerns dissolve once you treat the nodecules op-graph as syntax
for a functional dataflow program: nodes are transformations, edges
are typed data (strips), time is a parameter, and the graph as a whole
is a *declaration of a function*, not an execution recipe. The
executor / scheduler is then "an implementation of that declaration"
— one of several possible implementations (functions, actors, an
in-process scheduler, a distributed worker pool), interchangeable.

This document sketches what needs to be declared, what falls out for
free once it is, and the smallest viable slice to ship first.

## 1. Premise (the framing)

- The op-graph is a *functional dataflow program*. Nodes are pure
  functions over their declared inputs.
- Edges between nodes are *strips* — named, time-indexed publications
  whose schema is known and whose access is typed.
- Time is a parameter into each function, not a side effect. A node
  invocation is `(node, window) → output`.
- The graph is a declaration. Executors are implementations of the
  declaration. Op-graph syntax, raw Python functions, an actor mesh,
  a JIT compile to dask / ray / SLURM — all interchangeable below
  the declaration line.

This matches the existing invariants on `feat/temporality` (additive,
window-keyed, no hidden mutability) and the abstraction-discipline rule
(no `if provider == "..."` branching — providers are sibling plugins).
The same principle applied to time: no `if is_deterministic:` branching
inside core — determinism is a structural property derived from
declarations, not a runtime flag.

## 2. What's already in place

`feat/temporality` + the seven PR commits on this branch give us:

- `TimeRange` / `TimeSource` — time as a value type.
- `ChunkedContext` extending `ExecutionContext`.
- `NodeSpec.reads_strips` / `writes_strips` — coarse declarations: a
  node names which strips it reads and writes.
- `cycle_validator.py` — uses those names to reject within-window
  same-strip read+write cycles.
- `StripRegistry` + `StripView` — runtime strip API with `latest`,
  `in_range`, `before`, `after`, `at`, ordinal indexing.
- `is_deterministic` flag on `NodeSpec` and on cache entries — used
  by `compute_ready_set` for scheduling decisions and by the cache
  for eviction policy.

What's *missing*: nodes name the strips they read, but not **how**
they read. `strip.before(self.window)` and `strip.in_range(self.window)`
are runtime-only. The validator can't see them. The cache can't reason
about which `(predecessor, window)` outputs feed which `(node,
window)` invocation.

## 3. The proposal: typed access-pattern declarations

Add an algebraic data type describing how a node accesses each strip
it reads. Two layers:

```python
# Time-class expressions. Symbolic; resolved at scheduling time
# against (self.window, current settled set).
class TimeExpr:
    SelfWindowStart
    SelfWindowEnd
    SelfWindowStart - Duration
    SelfWindowEnd - Duration
    AbsoluteMs(int)
    # SelfWindowEnd + Duration intentionally absent. Forward-time
    # reads must be expressed via a separate "post-settling" edge,
    # which is a different node-level construct.

# Ordinal-class expressions. Resolved against the strip's
# materialized event list at scheduling time.
class IndexExpr:
    SelfElementIndex                  # whose? The strip-writing-loop
                                      # of THIS node. Self-write only.
    SelfElementIndex - PositiveInt    # n-1, n-2, ...
    AbsoluteIndex(int)                # rare; mostly for tests
    # SelfElementIndex + anything intentionally absent. Forward
    # self-reference is structurally rejected.

# Access patterns. Each is a typed read shape over a strip.
class AccessPattern:
    Latest                                       # strip.latest()
    Range(start: TimeExpr, end: TimeExpr)        # strip.in_range(...)
    Before(at: TimeExpr)                         # strip.before(...)
    After(at: TimeExpr)                          # strip.after(...)
    OrdinalAt(index: IndexExpr)                  # strip[k]
    OrdinalRange(start: IndexExpr, end: IndexExpr)
    SelfRelativeOrdinal(offset: PositiveInt)     # strip[me - n]
```

A node declares zero or more `StripAccess(strip_name, pattern)` entries.
Declaration is opt-in: a node with no declared patterns falls back to
the existing runtime strip API and the executor treats it as
unanalyzable (works, but doesn't get the static guarantees below).

The literal Python shape is sketchy here — the actual encoding could
be Pydantic models, frozen dataclasses, or string-DSL parsed at
graph-load. Pydantic is probably right because the graph JSON
round-trip already uses Pydantic for everything else and these need to
survive the executor ↔ frontend boundary.

## 4. What falls out

### 4.1 Static acyclicity, not just "no same-strip read+write"

Today's `cycle_validator` rejects "node A reads strip X and writes
strip X." That's coarse; it can't tell that `strip.before(self.window)`
is fine (cross-window) but `strip.in_range(self.window)` on the same
strip the node writes is a cycle.

With access patterns, build a `(node_id, time_class)` reachability
graph where `time_class ∈ {past, present, future}` relative to `self`.
An edge `A → B` exists iff B's declared access on a strip A writes
resolves to a `time_class` that overlaps `present`. Cycles in the
`present`-only subgraph are the actual rejection condition. The
`past`-only subgraph is always acyclic by temporal monotonicity.

This makes the existing "cross-window feedback via `strip.before()`"
escape hatch *structural* instead of *idiomatic*. The validator
doesn't have to special-case `before()` — `Before(SelfWindowStart)`
resolves to `time_class=past`, full stop.

### 4.2 Reachability-based cache lifecycle (no eviction policy)

For each `(node, window)` invocation, the declared access patterns
give you the exact set of `(predecessor_node, predecessor_window)`
outputs that feed it. Call this `inputs(N, W)`.

A cache entry for `(M, V)` is *reachable* iff there exists some
`(N, W)` that is either:

- currently pending in the scheduler, OR
- in the "possible replay set" (= within the user-configured replay
  horizon for the meeting, or marked as live by an annotation pin)

… such that `(M, V) ∈ closure(inputs(N, W))`.

When reachability drops to zero, the entry is forgotten. **There is no
eviction policy** — no `max_entries`, no `max_size_bytes`, no LRU
sweep, no `is_deterministic` flag. The cache is a memoization table
for a function whose dependency closure is computable.

Storage pressure becomes a scheduler concern: "the replay horizon is
N seconds wide; reduce N if memory is tight." That's a configuration
knob, not a policy buried in the cache.

The off-by-one in `8b1c5cd` is unrepresentable here. There's no loop
to get wrong; there are no flags to branch on; there's no shared
storage with policy.

### 4.3 "Deterministic" disappears as a runtime flag

In the current model, `is_deterministic: bool` is set at node spec
time and propagated through cache puts. It's used:

- by the cache for eviction policy ("pin nondet entries"),
- by `compute_ready_set` for scheduling decisions,
- by stenota for cache-key stability claims.

Under typed declarations, all three uses become structural:

- **Cache:** no policy → no flag needed. Nondet entries are reachable
  as long as something downstream might consume them; otherwise they're
  forgotten, same as anything else.
- **Scheduling:** a node is deterministic iff every input it declares is
  itself deterministic *and* its declared inputs fully cover its
  function's domain. A node that calls an LLM is non-deterministic
  because its dependencies aren't fully declared — the LLM is an
  external effect not appearing in the input graph.
- **Cache-key stability:** a node's cache key is stable iff all of its
  declared inputs hash stably. Static-DAG cache keys stay byte-stable
  exactly when the node has no temporal access patterns (`Latest`,
  `Range`, etc. all absent).

So `is_deterministic` becomes a derived predicate, not a per-node bool.
It can still be exposed as a property for introspection / UI, but it's
no longer load-bearing.

### 4.4 Settled is a property of strip × window

A strip is *settled* at window W iff every node that writes it has
completed its W invocation (or never writes for W — write declarations
include their window predicate). The `settling_windows` field PR-n7
added gives the upper bound: a strip is observable at W only after
`now ≥ W.end + settling_windows[strip] · window_size`.

This is just the temporal monotonicity rule made explicit. With typed
access patterns, you can compute "which windows of strip X must be
settled before node N can run for window W" directly from N's
declarations. The scheduler's wait-set is mechanical, not heuristic.

### 4.5 Annotation invalidation composes cleanly

An annotation that smudges `(N, W)` invalidates its cache entry. The
reachability closure says: *everything reachable from `(N, W)` in the
forward direction is also invalid.* So the smudge propagates without
the scheduler maintaining a separate invalidation tracker. The
existing mutability tags (`wet | drying | dry | smudged`) become UI
projections of reachability state — which matches the
"mutability-is-figurative" position already in `MEMORY.md`.

## 5. What this doesn't break

- **Static-DAG path:** no changes. Nodes without declared patterns
  keep using the runtime strip API and the existing executor. They
  just don't participate in the static guarantees.
- **Stenota's current nodes:** continue to work. `ctx.strip(name).
  before(self.window)` still resolves at runtime. Static analysis
  silently skips undeclared nodes.
- **The 240 temporal tests:** the ADT is additive. Tests that don't
  reference it continue to pass unchanged.
- **CLAUDE.md invariants:** all preserved. In particular: time stays
  integer ms (TimeExpr operates on integer ms); no provider-name
  branching is introduced; the core stays DB-free; existing cache
  keys remain byte-stable for nodes that don't declare patterns.

## 6. Smallest viable slice

Four PRs, in order. Each independently mergeable, each shippable
without the next.

### PR-r1: AccessPattern ADT (substrate only)

- Define `AccessPattern`, `TimeExpr`, `IndexExpr` in
  `core/strips.py` (or a new `core/strip_access.py` if that file gets
  big). Pydantic models so they round-trip through graph JSON.
- Add `reads_strip_patterns: list[StripAccess]` to `NodeSpec`,
  default `()`. Empty list = "uses runtime API only."
- Tests: ADT construction, serialization, equality, the obvious
  rejection cases (`SelfElementIndex + 1` should fail to construct,
  `SelfWindowEnd + Duration` should fail to construct).
- No scheduler changes. No cache changes. No stenota changes.
- ~300 lines + ~150 lines of tests.

This is the substrate. Nothing downstream lands without it.

### PR-r2: cycle_validator extension

- For nodes with declared patterns, build the `(node, time_class)`
  graph and reject `present`-only cycles.
- Backwards-compatible: undeclared nodes use the existing coarse
  same-strip read+write check.
- Tests synthesize small graphs at the validator level (no executor
  needed).
- ~150 lines + ~250 lines of tests.

### PR-r3: reachability-based cache

- New module `core/reachability_cache.py` (parallel to existing
  `node_cache.py`; coexist during migration).
- API: `retain(key, by=consumer_id)`, `release(by=consumer_id)`,
  `get(key)`, `put(key, value)`. No `max_entries`, no `max_size`.
- Backed by an in-memory map; filesystem backend lands as a follow-up.
- Scheduler integration: `compute_ready_set` calls `retain` when a
  consumer enters the pending set, `release` when it completes or
  passes the replay horizon.
- The existing `InMemoryNodeCache` / `FilesystemNodeCache` stay
  callable for the static-DAG path until migration completes.
- ~400 lines + ~300 lines of tests.

After r3 lands, the surgical fix in `8b1c5cd` can be removed along
with the rest of the policy-based eviction code. The deletion is the
point.

### PR-r4: stenota migration

- Replace stenota's runtime `ctx.strip(name).<method>` calls with
  declared patterns where the access shape is static (most L2/L3a
  reads). Keep runtime API for nodes whose access shape is genuinely
  dynamic (annotation-driven re-anneal, user-tool sidecar queries).
- Bumps node versions where output cache keys change. Migration
  notes in `stenota/CHANGELOG.md`.
- Coordinated with the deferred `PR-s2b` (registry kwarg threading).

## 7. Relationship to deferred PRs

- **PR-n7b (scheduler-v2 rewrite).** The dirty-queue + hare/tortoise
  design needs to know which `(node, window)` pairs are dirty. With
  reachability cache, dirtiness is "entries whose inputs changed
  since last cook." So `PR-r3` is the substrate `PR-n7b` rests on.
  Land r3 first.
- **PR-n9b (concrete LLM adapters).** Orthogonal. Not blocked.
- **PR-s2b (stenota ctx.strip migration).** Largely subsumed by PR-r4.
  Could ship s2b first if needed for unblocking; r4 supersedes the
  unfinished half later.

## 8. Open questions (worth thinking about before code)

- **IndexExpr vs TimeExpr.** Strip self-reference (`strip[me - 1]`)
  is ordinal; cross-strip reads are time-ranged. Do they unify under
  one expression algebra, or stay separate? Probably separate —
  ordinal makes no sense across strips with different event cadences.
- **Static-DAG as a special case.** A static node is one whose every
  access resolves to "the full present window." Could static and
  temporal share one executor, or do they stay separate for
  performance reasons? PR-n7b is the natural place to revisit.
- **Annotation pinning vs replay horizon.** If a user pins an
  annotation at `(t1, t2)` for review, what's the reachability
  implication? Probably: "treat `(t1, t2)` as in the pending set
  indefinitely." Needs a UI surface to express un-pinning.
- **External-effect declarations.** Right now LLM-using nodes are
  marked nondet via the `is_deterministic=False` flag. Under the new
  model, they need an explicit `external_effects: list[EffectRef]`
  declaration so the structural-determinism predicate has something
  concrete to read. The exact schema for `EffectRef` (provider name?
  capability tag?) wants its own short design discussion.
- **What about the existing `node_cache.py`?** It stays put through
  PR-r3 to keep the static-DAG path runnable. Removal lands with
  PR-r4 once stenota is migrated. Total deletion: probably ~300 lines
  of code + ~500 lines of tests no longer needed.

## 9. Connections to existing MEMORY entries

This proposal is consistent with several previously-recorded
positions:

- **Abstraction discipline** — "no `if provider == ...` in core; providers
  are sibling plugins." Generalizes to: no `if is_deterministic:` in
  core; determinism is structural.
- **Annealing not constraints** — "labels are observations, not
  clustering constraints." Generalizes to: cache entries are
  observations of a function's outputs, not state in a state machine.
  Reachability replaces explicit state.
- **Mutability is figurative** — "wet / drying / dry / smudged is a
  UI tag, not a state machine; re-cooking = cache invalidation."
  Reachability-based forgetting makes that literally true: the UI tag
  is a projection of reachability + last-write-time.
- **Chunk size principle** — "chunk size is tractability, not
  granularity; calibrate so larger chunks give the same result."
  Static access patterns express this: a coarsening that preserves
  reachability is a pure performance change, not a semantic change.

## 10. tl;dr

The cache off-by-one fixed in `8b1c5cd` is the surface symptom. The
underlying issue is that we're trying to manage storage policy
imperatively over a store whose membership rules are functionally
determined by the surrounding graph. Once strip accesses are typed
expressions, both cycle detection and cache lifecycle fall out
mechanically, and several existing concerns (the `is_deterministic`
flag, the cross-window-feedback escape hatch in cycle_validator, the
mutability state machine) collapse into structural properties.

Concrete next step: PR-r1 — the AccessPattern ADT, declarative-only,
no behavior change. Everything else composes on top.

If you've gotten this far and the framing rings true: revert
`8b1c5cd` when PR-r3 lands; until then it's a stopgap.

If parts of the framing feel off, flag them in the chat and we'll
discuss before any code.
