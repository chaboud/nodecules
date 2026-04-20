# CLAUDE.md — nodecules

Instructions for Claude Code working in this repo. Read `README.md` before making any non-trivial change, and `TEMPORALITY.md` if you are touching anything on the `feat/temporality` branch.

This file is *constraints and invariants*, not task lists.

## Repo at a glance

- **Backend** — FastAPI + SQLAlchemy + PostgreSQL + Redis. Source under `backend/nodecules/`. Entry point `nodecules/main.py`. Docker-compose stack in repo root.
- **Frontend** — React 18 + React Flow + Tailwind in `frontend/`. Graph editor UI.
- **Plugins** — autodiscovered Python files under `plugins/` (repo root) or `backend/plugins/`. Any `*.py` with `BaseNode` subclasses is picked up at startup.
- **Core types** — `backend/nodecules/core/types.py` (dataclasses: `NodeSpec`, `NodeData`, `EdgeData`, `GraphData`, `ExecutionContext`, `BaseNode`).
- **Execution engine** — `backend/nodecules/core/executor.py` (Kahn's-algorithm topological sort, async, supports streaming).
- **Provider adapters (chat-shaped)** — `backend/nodecules/core/smart_context.py` with Ollama, Anthropic, Bedrock, Mock adapters. Used by chat nodes.
- **Content-addressable chat contexts** — `backend/nodecules/core/content_addressable_context.py`. Postgres-backed store keyed by `sha256(messages)[:16]`. This is a chat-message-history cache, **not** a general node-output cache.

## Hard invariants (violations require explicit discussion in the PR)

1. **The static-DAG execution path does not regress.** Every existing node, every existing example graph, every existing test must keep working before and after any change. If an existing test fails, the change is wrong — do not "update the test to match."
2. **Additive changes to core types.** New fields on `NodeSpec`, `NodeData`, etc. must have defaults so existing construction sites keep working. Do not reorder dataclass fields.
3. **Plugin auto-discovery must keep working.** Never introduce a change that forces every plugin to be rewritten to load.
4. **Core library works without Postgres/Redis.** Features that require the DB or Redis live behind the FastAPI layer (`api/`, `services/`), not in `core/`. The core should be importable and runnable from a CLI against only the filesystem.
5. **No provider names in orchestration code.** `core/` and the executor never branch on `provider == "ollama"` or equivalent. Provider selection rides on graph JSON params and the plugin registry.
6. **Graph-as-JSON stays declarative.** Don't introduce Turing-complete JSON or execution logic inside graph payloads.

## Temporality (feat/temporality branch)

**Read `TEMPORALITY.md` before writing any code on this branch.**

### Hard invariants for this branch

1. **Additive only.** The existing static-DAG execution path must behave identically before and after any change. `temporal_kind` defaults to `"static"`.
2. **`temporal_kind` defaults to `"static"`.** Every existing node, every existing graph, every existing plugin keeps working without modification.
3. **Time is integer milliseconds, meeting-relative.** No floats. No Unix epoch in the pipeline. Epoch timestamps get converted at ingest and at render, never in between.
4. **`TimeSource` is injected, not global.** Never call `time.time()` or `datetime.now()` in the scheduler or in temporal nodes. Always go through the `TimeSource`.
5. **Cache keys include `window_hash` and `annotation_hash` only for temporal nodes.** Static-node cache keys are unchanged; they must produce the same hash they did before this branch.
6. **Scheduler is single-threaded for now.** Do not introduce parallel window execution, concurrent `run_single` calls, or shared-state mutation from multiple tasks. Correctness before concurrency.
7. **`TemporalScheduler` wraps the existing executor; it does not replace it.** `run_single` on the existing executor is the primitive. The scheduler's job is to decide *which* `(node, window)` to hand it next.
8. **Annotations live in nodecules as a base type.** Concrete annotation payloads are defined by consumers (stenota). Do not hardcode meeting-specific annotation kinds here.
9. **Core library works without the DB.** The node-output cache layer added on this branch must have a filesystem-only backend. Redis and Postgres are optional.

### What belongs on this branch

- `nodecules/core/time.py` — `TimeRange`, `TimeSource`, `WallClock`, `FileClock`, `ManualClock`
- `nodecules/core/temporal_context.py` — `ChunkedContext` extending `ExecutionContext`
- `nodecules/core/types.py` additions — `WindowSpec`, new `NodeSpec` fields, output `mutability` literal
- `nodecules/core/node_cache.py` — content-addressable node-output cache (filesystem backend primary; optional Redis)
- `nodecules/core/annotations.py` — `AnnotationNode` base, annotation index
- `nodecules/core/scheduler.py` — `TemporalScheduler` and `compute_ready_set`
- Tests under `backend/tests/temporal/`

### What does NOT belong on this branch

- Meeting-specific node types (ASR, diarization, VLM, summarizers). Those are stenota's job.
- New LLM provider adapters. Unchanged.
- Frontend changes (the React Flow editor). A later branch, after the backend primitives stabilize.
- Changes to the plugin auto-discovery mechanism.
- New graph JSON format version. Temporal info rides on `NodeSpec`, not the graph envelope.

### Testing requirements before requesting merge to `main`

1. Full existing test suite passes unchanged (static-DAG regression guard).
2. New tests under `backend/tests/temporal/` covering:
   - `TimeRange` edge cases
   - `FileClock` + `TemporalScheduler` integration
   - Annotation invalidation (smudge + re-run)
   - Emit policy semantics (`streaming`, `on_window_close`, `on_graph_close`)
   - Cache-key stability for static nodes (regression guard)
3. At least one end-to-end test with a synthetic windowed graph running over a mock input stream using `FileClock`.
4. `poetry run pytest` exits cleanly; `poetry run mypy nodecules/` exits cleanly (new code is typed).

### What to do when uncertain

- **Uncertain whether a change is additive?** Run the existing test suite. If anything fails, it's not additive.
- **Uncertain whether something should be in nodecules or stenota?** If it's useful to a graph that isn't about meetings, it's nodecules. Otherwise, stenota.
- **Uncertain whether to add a field to `NodeSpec`?** Default to no. Raise a discussion in the PR first. Every `NodeSpec` field ripples to every plugin.
- **Uncertain about a scheduler corner case?** Write the test case first, then make it pass. `compute_ready_set` has a lot of subtle semantics; tests are how you pin them down.

### Coordination with stenota

- Stenota pins nodecules by git SHA on this branch during development.
- When you land a change that stenota needs, bump stenota's pinned SHA in a companion PR.
- Do not merge `feat/temporality` to `main` until at least one stenota milestone (v0.1) has successfully run against a late-branch SHA end-to-end. This is your integration canary.

## Code conventions

- Python 3.11+. Pydantic v2 for schemas. `async` throughout the node layer.
- Dataclasses are fine for existing core types (`NodeSpec`, `NodeData`). New temporal types may be Pydantic models when JSON round-tripping matters (`TimeRange`, `WindowSpec`).
- Type hints are mandatory on public APIs.
- Node versions are strings of the form `major.minor.patch` and must bump on any change that affects output (not just behavior — output). Cache keys depend on version; stale cache is worse than no cache.
- Timestamps in temporal code are integer milliseconds. Never floats. Never Unix epoch mid-pipeline.
