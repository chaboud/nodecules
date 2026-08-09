# CLAUDE.md — nodecules

Orientation for any Claude Code instance working in this repo. Several
instances work here from different machines; **read the branch map before you
commit anything.**

## What this repo is, honestly

A node-based graph processing engine: React Flow editor, FastAPI + SQLAlchemy +
Postgres + Redis backend, multi-LLM support. Its own README says "[under
construction and **very** unstable]", and that is accurate.

**It is also now the designated vehicle for a new distributed compute
substrate.** The decision is: *nodecules is the vehicle; the codebase is new.*
Same repo, same name, same relationship to stenota — a fresh spine underneath.
Nothing on `main` is load-bearing for that work.

## Branch map — read this first

| branch | what it is | who touches it |
|---|---|---|
| `main` | the original engine. Working, unstable, partly aspirational docs. | leave alone unless fixing `main` |
| `feat/temporality` | **the good stuff.** Temporal primitives being hardened for stenota: `TimeRange`, `TimeSource`, `ChunkedContext`, `TemporalScheduler`, node cache, annotations. ~4,050 lines, ~1,900 of them tests. Contains `main`. | stenota-driven work |
| `claude/llm-wiki-distributed-compute-*` | design spikes for the new substrate. Touches **only `spikes/`**. | cloud instance |

Conflict surface between the substrate spikes and everything else is currently
**zero** — the spike branch adds `spikes/` and one `.gitignore` line, nothing
more. Keep it that way: if substrate work needs to touch `backend/`, branch
fresh from `feat/temporality` rather than piling onto the spike branch.

## Known traps

**`ARCHITECTURE.md` and `architecture.md` are two different files** (233 and 199
lines, different content) that **collide on a case-insensitive filesystem**.
Checking this repo out on macOS will produce phantom modifications or silently
lose one. This needs resolving before anyone works from a Mac — pick one, delete
or rename the other, in a commit that does nothing else.

**`plan-of-record.md` claims "🟢 FULLY FUNCTIONAL SYSTEM / Production-Ready".**
It is not. Treat it as aspiration, not inventory. Same for `planning/*.md`.

**Verified broken or absent on `main`** (audited 2026-07-16, re-verified
2026-07-29 by reading the source):

- `core/context_service.py` **does not exist** but is imported at four call
  sites in `api/executions.py` — the continue/rewind path raises on first use.
- `ResourceRequirement` is declared on every `NodeSpec` and read by **nothing**.
  Grep finds one unused import. It is the repo's own cautionary tale: a
  declaration nothing enforces decays into decoration within a release.
- `execute_parallel_batches` is *fully implemented* and has **no caller**.
- No auth, no WebSocket endpoints, no tool/function calling, no pub/sub
  (celery is declared and never imported).
- No meaningful test suite on `main` — three ad-hoc scripts. (`feat/temporality`
  is the opposite: 7 test modules under `backend/tests/temporal/`.)

## What `feat/temporality` already got right

Worth knowing before redesigning anything, because it anticipated several
decisions:

- **`core/node_cache.py`** keys on `(node_type, node_version, params_hash,
  input_hashes, window_hash, annotation_hash)` — "what code + what knobs" plus
  "what did it read", with window/annotation hashes isolating temporal slices so
  a change at window W doesn't invalidate windows that aren't at W. Canonical
  JSON, sha256, atomic filesystem writes. Port order is deliberately part of the
  key, "since port order is part of the node's observable interface."
- **`core/time.py`** has a `TimeSource` protocol (`WallClock` / `FileClock` /
  `ManualClock`) and the rule that schedulers and temporal nodes never call
  `time.time()` directly. That is a clock treated as a declared, swappable
  dependency rather than ambient authority.
- **`core/annotations.py`** makes an annotation a first-class node whose content
  hash participates in the cache key of every downstream node whose window
  intersects it — so adding an annotation invalidates exactly the affected
  subgraph, and removing it invalidates the same one.
- **`core/scheduler.py`** is honest about scope: streaming *raises at
  construction*, live mode is later, no parallel windows, no backpressure.
  "Scope discipline: batch first."

**The known gap:** `node_version` is a *declared string*, not a hash of the
code. A version string is a promise; a hash is a fact. A node whose behaviour
changes without a version bump silently poisons every result cached against it.
Closing this is small and should happen early — see `spikes/identity-bench/`,
which measures the alternatives.

## `spikes/`

Throwaway benches that put numbers against design assertions. **Not the new
core, not a dependency of anything.** Delete freely once their findings are
absorbed. See `spikes/README.md`.

## Design authority

Architecture decisions live in the **ChaboudPrivateWiki** vault
(`LLM_Wiki/decisions/`, ADRs 0002–0016), not here. If you have that vault,
read `LLM_Wiki/primitive/index.md` first. If you don't, the load-bearing ones,
compressed:

- **Identity is two layers.** `hash(code) + hash(data)` composes to a graph hash
  that is fully reproducible and *is* the cache key. Above it, a semantic layer
  makes elements interchangeable; substitution deliberately changes the hash, so
  a different realization of the same intent is visible rather than silent.
- **Default to perturbing.** Believing two things are the same when they aren't
  is silent and unrecoverable; believing they differ when they don't costs a
  recompute. Clocks, novel raw sources, unseeded *and* non-re-seeded PRNGs,
  mutation, and IIR feedback all perturb. Determinism and coverage are two
  separate properties — a node can be deterministic and still unsafe if its
  inputs aren't all inside the hash.
- **Binding is negotiation over a graph, and workers may rewrite it.** You
  submit intent; a worker returns the result plus the composed hash of the graph
  it actually ran. No central registry — schemas are data.
- **Binding granularity is a region, not a node.** Crossing a domain boundary
  costs latency *and* precision, so per-node choice optimises one axis and
  silently blows the other.
- **The portable unit of compute is an *ingot*** — a WASM reference realization,
  superseded by hardware-specific *castings*. A WASM module's import section is
  a sandbox-enforced proof of everything it can reach, and the ingot doubles as
  the conformance oracle for its castings.

## Working rules

- **Read the code, not the planning docs.** This repo is the reason that rule
  exists in the first place.
- Say what is true about status: *shipped*, *working*, *designed*, and
  *aspirational* are four different things.
- If you add a declaration (a resource requirement, a capability, a version),
  make something *consume* it in the same change, or don't add it yet.
- Commit in coherent units. Push to your own `claude/*` branch; don't push to
  `main` or `feat/temporality` without saying so.
