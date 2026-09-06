# CLAUDE.md — nodecules

Instructions for Claude Code working in this repo. Several instances work here
from different machines — **read the branch map before you commit anything.**
Read `README.md` before making any non-trivial change, `TEMPORALITY.md` before
touching the temporal machinery, and `REFERENCE-MODEL.md` for the substrate
design this repo is becoming the vehicle for.

This file is *constraints and invariants*, not task lists.

## What this repo is

A node-based graph processing engine: React Flow editor, FastAPI + SQLAlchemy +
Postgres + Redis backend, multi-LLM support. Its own README says "[under
construction and **very** unstable]", and that is accurate for `main`.

**It is also the designated vehicle for a new distributed compute substrate.**
The decision is: *nodecules is the vehicle; the codebase is new.* Same repo,
same name, same relationship to stenota — a fresh spine underneath. Nothing on
`main` is load-bearing for that work.

**Naming (vault ADR-0020, 2026-08-25):** the substrate *is* nodecules — the
system name, not just the repo name. Convention: **"nodecules" unqualified
means v2** (the substrate specified in `REFERENCE-MODEL.md`); the original
engine on `main` is always called **the legacy engine**, qualified. Don't
introduce new placeholder names ("the primitive", "the substrate" as a proper
noun). New mechanism names may draw on the chemistry family (valence = a
kind's reference constraints, bond = typed edge, isomer = equivalent graph
realization, polymer/monomer = strip/cell) alongside the committed metallurgy
terms (ingot, casting, anneal, assay, hallmark). Plain words first: every
such term appears next to its plain meaning the first time a page or
docstring uses it (founder steer, 2026-09-06: "cut some of the esoterica").

**What it must serve (founder, 2026-09-06).** Seven consumers, one
substrate — the vault's `LLM_Wiki/primitive/consumers.md` carries the
matrix. In the founder's order: generic software and LLM interaction
systems that can self-modify and self-distribute; a better stenota (graphs
that turn strips into other strips, for compact inference); a better
keyhole; a better Alexa, or a shared play-space for several people and
several LLMs at once; intelligent distribution of the compute and data
under all of those; rentable inference on open-weights models with
attestation, so those functions become commodities; and declarative
inference services over models and data a provider does not share, classic
API style. **A primitive that serves only one of these is a consumer
feature and belongs in that consumer.** Don't over-focus on one subset;
don't cut straight lines through the architecture — each abstraction here
has a stated reason to exist, and the reason is usually a consumer that is
not the one currently being grounded against.

## Branch map — read this first

| branch | what it is | who works it |
|---|---|---|
| `main` | the original engine. Working, unstable, partly aspirational docs. | leave alone unless fixing `main` |
| `feat/temporality` | temporal primitives for stenota: `TimeRange`, `TimeSource`, `ChunkedContext`, `TemporalScheduler`, node cache, annotations. Contains `main`. Fast-forwarded to track the substrate work below. | stenota-driven work |
| `claude/recon-nodecules-t9Dqq` | the laptop's substrate line: `REFERENCE-MODEL.md` (declarative generation DAG over a COW node store) plus shipped code: strips, typed access patterns (PR-r1), the resolver (PR-r2), subscriptions, environment, tool-aware providers, cycle validator — 279 tests, no DB required. Descends from `feat/temporality`. | laptop instance |
| `claude/nodecules-v2-naming-matching-vmkexv` | **the live cloud-side line.** Everything above plus `spikes/` (identity-bench E1–E7, matching-bench M1–M8) and this file. Supersedes `claude/llm-wiki-distributed-compute-ii9ijq`, which it contains. | cloud instance |

Cloud session branches get renamed by the harness between sessions, and a
handoff has twice deleted the old remote branch and re-seeded the new name
from `main` — **if a "cloud" branch looks like bare `main`, look for the
newest `claude/*` branch containing the spikes before assuming work is
lost** (recovery precedent: vault unification plan, 2026-08-29).

Design authority lives in the **ChaboudPrivateWiki** vault
(`LLM_Wiki/decisions/`, ADRs 0002–0023; 0018 one-node-model blessed with
residency conditions, 0019 escrow execution, 0020 naming, 0021 the
satisfies judgment, 0022 reputation-is-layered, 0023 the store slice). If you have the vault,
read `LLM_Wiki/state-of-play.md` first, then `LLM_Wiki/primitive/index.md`.
The compressed load-bearing decisions are at the bottom of this file for
instances that don't.

## Repo at a glance

- **Backend** — FastAPI + SQLAlchemy + PostgreSQL + Redis. Source under `backend/nodecules/`. Entry point `nodecules/main.py`. Docker-compose stack in repo root.
- **Frontend** — React 18 + React Flow + Tailwind in `frontend/`. Graph editor UI.
- **Plugins** — autodiscovered Python files under `plugins/` (repo root) or `backend/plugins/`. Any `*.py` with `BaseNode` subclasses is picked up at startup.
- **Core types** — `backend/nodecules/core/types.py` (dataclasses: `NodeSpec`, `NodeData`, `EdgeData`, `GraphData`, `ExecutionContext`, `BaseNode`).
- **Execution engine** — `backend/nodecules/core/executor.py` (Kahn's-algorithm topological sort, async, supports streaming).
- **Temporal core** — `core/{time,temporal_context,node_cache,annotations,scheduler,strips,strip_access,strip_resolve,subscriptions,environment,cycle_validator,events}.py`. Tests under `backend/tests/temporal/` (pytest + pydantic only; no DB).
- **Substrate core (nodecules v2)** — `core/descriptions.py` + `core/assay_metrics.py` (PR-d1), `core/placement.py` (PR-d2), `core/pmap.py` + `core/store.py` (PR-r3a: the node store — manifests, CAS transactions with a per-scope resolution policy, envelopes, residency, composed hash), `core/timeline.py` (timebases, timelines, skew maps). Same test tree, same no-DB rule.
- **Provider adapters (chat-shaped)** — `backend/nodecules/core/smart_context.py` with Ollama, Anthropic, Bedrock, Mock adapters. Used by chat nodes. The tool-aware abstraction is `core/llm_providers.py`.
- **Content-addressable chat contexts** — `backend/nodecules/core/content_addressable_context.py`. Postgres-backed store keyed by `sha256(messages)[:16]`. This is a chat-message-history cache, **not** the general node-output cache (that is `core/node_cache.py`).
- **Spikes** — `spikes/` holds throwaway design benches with measured findings. Not core, not imported by anything. See `spikes/README.md`.

## Hard invariants (violations require explicit discussion in the PR)

1. **The static-DAG execution path does not regress.** Every existing node, every existing example graph, every existing test must keep working before and after any change. If an existing test fails, the change is wrong — do not "update the test to match."
2. **Additive changes to core types.** New fields on `NodeSpec`, `NodeData`, etc. must have defaults so existing construction sites keep working. Do not reorder dataclass fields.
3. **Plugin auto-discovery must keep working.** Never introduce a change that forces every plugin to be rewritten to load.
4. **Core library works without Postgres/Redis.** Features that require the DB or Redis live behind the FastAPI layer (`api/`, `services/`), not in `core/`. The core should be importable and runnable from a CLI against only the filesystem. (Known bounded exceptions: `smart_context`, `content_addressable_context`, `instance_executor` — tracked in `TODO.md`, quarantined by `plugins/service_nodes.py`.)
5. **No provider names in orchestration code.** `core/` and the executor never branch on `provider == "ollama"` or equivalent. Provider selection rides on graph JSON params and the plugin registry.
6. **Graph-as-JSON stays declarative.** Don't introduce Turing-complete JSON or execution logic inside graph payloads.

## Temporality

**Read `TEMPORALITY.md` before writing any code that touches the temporal
machinery.** Its hard invariants, condensed:

1. **Additive only.** `temporal_kind` defaults to `"static"`; every existing node, graph, and plugin keeps working unmodified.
2. **Time is integer ticks on a named timeline with an exact rational timebase** (`core/timeline.py`); the meeting timeline is 1/1000 and every `_ms` field keeps meaning milliseconds. No floats. No Unix epoch mid-pipeline — an epoch is a timeline you convert through a `TimelineMap`, visibly. Two clocks never compare without a map.
3. **`TimeSource` is injected, not global.** Never call `time.time()` or `datetime.now()` in the scheduler or in temporal nodes.
4. **Cache keys include `window_hash` and `annotation_hash` only for temporal nodes.** Static-node cache keys must produce the same hash they did before the branch.
5. **Scheduler is single-threaded for now.** Correctness before concurrency.
6. **`TemporalScheduler` wraps the existing executor; it does not replace it.**
7. **Annotations live in nodecules as a base type.** Concrete payloads are consumers' (stenota's) job.
8. Meeting-specific node types, new provider adapters, frontend changes, and graph-format changes do **not** belong on the temporality surface.

### Coordination with stenota

- Stenota pins nodecules by git SHA during development. When you land a change stenota needs, bump stenota's pinned SHA in a companion PR.
- Do not merge the temporal work to `main` until at least one stenota milestone (v0.1) has run against a late SHA end-to-end. That is the integration canary.
- Per the vault's ADR-0011: **no migration is owed to existing consumers** — they get regenerated on the new foundation once it is proven, not ported mid-flight.

## Known traps

- **`ARCHITECTURE.md` and `architecture.md` are two different files** (233 and 199 lines, different content) that collide on a case-insensitive filesystem. The laptop works on case-sensitive APFS (`/Volumes/case`), so it does not bite there — but any ordinary macOS checkout will silently corrupt one. Resolution should happen on the case-sensitive machine, in a commit that does nothing else, and the problem is wider than two files (`backend/architecture.md`, `frontend/architecture.md`, `planning/*-architecture-doc.md`).
- **`plan-of-record.md` claims "🟢 FULLY FUNCTIONAL SYSTEM / Production-Ready".** It is not. Treat it and `planning/*.md` as aspiration, not inventory.
- **Verified broken or absent on `main`**: `core/context_service.py` does not exist but is imported at four call sites in `api/executions.py`; `execute_parallel_batches` is fully implemented with no caller; no auth, no websockets, no tool calling on `main`; `ResourceRequirement` is declared on every `NodeSpec` and read by nothing — the repo's own cautionary tale that a declaration nothing enforces decays into decoration within a release.

## Code conventions

- Python 3.11+ (3.12 in CI use). Pydantic v2 for schemas. `async` throughout the node layer.
- Dataclasses are fine for existing core types (`NodeSpec`, `NodeData`). New temporal types may be Pydantic models when JSON round-tripping matters (`TimeRange`, `WindowSpec`).
- Type hints are mandatory on public APIs.
- Node versions are strings of the form `major.minor.patch` and must bump on any change that affects output. Cache keys depend on version; stale cache is worse than no cache. **Agreed direction:** replace the declared string with an AST hash of the node's code (`include_attributes=False`), shipped together with a coverage-gap report — measured in `spikes/identity-bench/` E1+E2. Until that lands, the string discipline is binding.
- Timestamps in temporal code are integer milliseconds. Never floats. Never Unix epoch mid-pipeline.

## Working rules

- **Read the code, not the planning docs.** This repo is the reason that rule exists.
- Say what is true about status: *shipped*, *working*, *designed*, and *aspirational* are four different things.
- If you add a declaration (a resource requirement, a capability, a version), make something *consume* it in the same change, or don't add it yet.
- Commit in coherent units. Push to your own `claude/*` branch; don't push to `main` or `feat/temporality` without saying so.

## Design authority, compressed

For instances without the vault. Full ADRs: ChaboudPrivateWiki
`LLM_Wiki/decisions/`.

- **Identity is two layers.** `hash(code) + hash(data)` composes to a graph hash that is fully reproducible and *is* the cache key. Above it, a semantic layer makes elements interchangeable; substitution deliberately changes the hash, so a different realization of the same intent is visible rather than silent.
- **Default to perturbing.** Believing two things are the same when they aren't is silent and unrecoverable; believing they differ when they don't costs a recompute. Clocks, novel raw sources, unseeded *and* non-re-seeded PRNGs, mutation, and IIR feedback all perturb. Determinism and coverage are two separate properties — a node can be deterministic and still unsafe if its inputs aren't all inside the hash. (The settling spectrum in `REFERENCE-MODEL.md` §17 refines the IIR case: recoverability follows from settling depth K, and determinism is decisive only at K = ∞.)
- **Substitutability is margin-relative.** The criterion is the implementation difference *relative to the decision margins* of the actual workload, and both are measurable. Rounding-scale substitution is safe even in discrete-stateful nodes; model-scale substitution is the risk.
- **Binding is negotiation over a graph, and workers may rewrite it.** You submit intent; a worker returns the result plus the composed hash of the graph it actually ran. No central registry — schemas are data.
- **Binding granularity is a region, not a node.** Crossing a domain boundary costs latency *and* precision; per-node choice optimises one axis and silently blows the other.
- **The portable unit of compute is an *ingot*** — a WASM reference realization, superseded by hardware-specific *castings*. A WASM module's import section is a sandbox-enforced proof of everything it can reach, and the ingot doubles as the conformance oracle for its castings.
- **Retention envelope ≠ fetch envelope.** What must still exist is not what a given consumer reads now; many fetch envelopes, one retention envelope. Checkpoint cadence is the floor of the retention envelope for stateful nodes.
- **The satisfies judgment is two judgments** (ADR-0021, measured in `spikes/matching-bench/`): a structural *valence check* over kinds plus an empirical *assay* against the reference realization a description ships with. The assay certifies a probed subset, never a universal claim — so probe provenance is a first-class receipt field, and probes drawn fresh from the live workload detect defection at exactly the harm rate (~3/f probes exclude defection above rate f). An *audition* is the same assay run consumer-side, with the auditioner's own known-output jobs.
- **Reputation is layered, never kernel** (ADR-0022): the substrate ships facts — identity, hallmarks, re-runnable assays, registries — and reputation mechanics are derived kinds folding over them. A vouch is a label: it cites a hash, nothing reachable from that hash cites it back, enforced as a valence constraint. No trust-score field in the node envelope, ever.
