# HANDOFF — verify PR-n4 through PR-n9 + PR-s2

**You are a fresh Claude instance.** A previous Claude (running without a
local repo, no ability to run tests) pushed seven commits to nodecules
and one to stenota across an overnight session. Your job is to pull
the branches, run the test suite, triage failures, and iterate. The
user is asleep but expects you to make progress autonomously — they
asked the previous Claude to "go to bed" so you have the run of the
place.

Read this whole file before pulling. ~10 min of reading saves hours of
guessing.

## Big picture (2 minutes)

nodecules is a node-graph compute engine in the ComfyUI family. It has a
`main` branch (production) and a `feat/temporality` branch (active dev,
adds windowed nodes + caching + annotations + event log). Stenota is a
meeting annotator that depends on nodecules.

Over the last several hours, the user designed a roadmap of work
extending nodecules toward what stenota needs for v0.2 (VLM +
attention-requests + L3a summarizer + observability) and beyond.
Most of the design recognizes patterns stenota has already organically
built; the work is *extraction and formalization*, not new construction
from scratch.

Seven PRs are planned: PR-n4 (strips + cache eviction), PR-n6
(subscriptions), PR-n7 (scheduler v2), PR-n8 (typed environment),
PR-n9 (tool-aware provider adapter), plus PR-s2 (stenota strip
registration) and a few `*b` follow-ups. **The previous Claude shipped
the additive parts of all seven**, deliberately deferring three things
that need real-environment validation:

- PR-n7b: actual scheduler-v2 rewrite (dirty queue, hare/tortoise,
  pre-roll, `run_batch` rename). Multi-week work; can't validate without
  running stenota.
- PR-n9b: concrete Ollama/Anthropic/Bedrock implementations of the
  ToolAwareProvider abstraction. Need real API access.
- PR-s2b: stenota node migration to `ctx.strip(...)` + scheduler kwarg
  wiring. Hot-path behavior change; needs a meeting run.

For full context: read `TEMPORALITY.md` (PR-n3 design) then
`TEMPORALITY-ROADMAP.md` (everything after).

## Branches + commits

Both repos use branch name `claude/recon-nodecules-t9Dqq`.

**nodecules** (off `feat/temporality`):

| Commit | PR | Scope |
|---|---|---|
| `5ecc15a` | n4 part 1 | NodeSpec gains is_deterministic/reads_strips/writes_strips; new `core/strips.py`; ChunkedContext.strip() |
| `3ca569e` | n4 part 2 | cache eviction (determinism-aware pinning); 36 tests |
| `e5998b6` | n4 part 3 | scheduler routes is_deterministic; 6 tests; roadmap stenota-lite framing |
| `a3193e3` | n6 | DerivationPhase enum; new `core/subscriptions.py`; ChunkedContext.subscribe/publish; 18 tests |
| `18090ef` | n8 | new `core/environment.py` (capabilities + sinks + with_overrides + validate_env_deps); ChunkedContext.cap/sink; 16 tests |
| `63ed0b6` | n9 | new `core/llm_providers.py` (ToolAwareProvider + Mock); 13 tests |
| `97f2062` | n7 scaffold | settling_windows on NodeSpec; new `core/cycle_validator.py`; 12 tests |

**stenota** (off `main`):

| Commit | PR | Scope |
|---|---|---|
| `e9edfcc` | s2 | new `stenota_graph/strips.py` (8 strip declarations + REGISTRY); `__init__.py` re-export; 11-case smoke test |

Every commit message documents its own scope precisely. Read them.

## Verify

```bash
# 1. nodecules
cd /path/to/nodecules
git fetch origin
git checkout claude/recon-nodecules-t9Dqq
git log --oneline feat/temporality..HEAD   # should show 7 commits

cd backend
poetry install
poetry run pytest tests/temporal/ -v       # 136 existing + ~101 new
poetry run mypy nodecules/core/            # should be clean

# 2. stenota (after nodecules verifies)
cd /path/to/stenota
git fetch origin
git checkout claude/recon-nodecules-t9Dqq
pip install -e /path/to/nodecules/backend
pip install -e ".[lite,dev]"
pytest tests/test_strips.py -v             # 11 cases
```

If you have a short meeting media file, also smoke-run stenota end to
end. Nothing in stenota_graph reads the new strip registry yet, so
this just verifies the existing pipeline still works:

```bash
stenota process /path/to/short_meeting.mp4 --sidecar /tmp/handoff.stenota
```

The sidecar should populate with `audio.wav`, `asr.jsonl`,
`diar.jsonl`, `claims/L2.jsonl`, and `renders/meeting.md`. Identical
to pre-handoff behavior.

## Likely bugs (the previous Claude couldn't test)

~4,000 lines of code + ~100 tests written without local execution.
Specific risks ranked by likelihood:

1. **Async test discovery.** `test_subscriptions.py`,
   `test_llm_providers.py`, and async tests in `test_is_deterministic.py`
   use `async def test_*` without `@pytest.mark.asyncio` decoration.
   They assume `asyncio_mode = "auto"` is configured globally. The
   existing `test_scheduler.py` uses the same pattern, so the
   configuration must exist somewhere — likely in
   `backend/tests/conftest.py` (508 bytes, the previous Claude didn't
   read it) or `backend/pyproject.toml`. **If async tests are being
   silently collected as empty, this is why.** Fix: add
   `asyncio_mode = "auto"` to `[tool.pytest.ini_options]` in
   `backend/pyproject.toml`, OR add `pytest_plugins = ["pytest_asyncio"]`
   and `@pytest.mark.asyncio` decorations to the new test classes.

2. **Import path drift.** All new modules import via
   `from nodecules.core.X import ...`. If any tests are running from a
   directory where `nodecules` isn't installed, they'll fail at
   collection. `poetry install` from `backend/` should fix.

3. **`StripView.before()` early termination.** In
   `backend/nodecules/core/strips.py:before()`, the previous Claude
   added an `elif evt_range.start_ms >= time_range.start_ms: break`
   that assumes JSONL arrival order ≈ time order. For pathological
   inputs (out-of-order JSONL), this could return wrong results.
   Stenota's append-on-emit pattern is in time order, so it's fine in
   practice; but a synthetic test that writes out-of-order would catch
   it. Worth knowing.

4. **`Subscription._sentinel`.** Uses `item is self._sentinel` identity
   check to signal close. Should work — `object()` returns a unique
   instance — but if the close behavior is wonky, look there.

5. **`Visibility` default phases.** Uses
   `field(default_factory=lambda: _DEFAULT_PHASES)` where
   `_DEFAULT_PHASES` is a module-level frozenset. Should be fine
   because the lambda returns the shared frozen instance; no
   per-instance mutation. If you see weird default-phase behavior
   across tests, check.

6. **`StripCycleError.__init__` ordering.** Sets `self.cycle` *before*
   `super().__init__(...)`. `ValueError.__init__` doesn't reset
   attributes (just stores `args`), so this should be safe. If repr
   looks weird, swap the order.

7. **Pydantic v2 method usage.** Used `model_validate_json`,
   `model_dump_json`, `ConfigDict(frozen=True)`. These require
   Pydantic v2. `feat/temporality` already uses v2 (see
   `core/annotations.py`), so the dep is in place. If v1, things
   break loudly.

8. **`StripSpec` equality.** Frozen dataclass with a
   `Callable[[Any], bool]` field. Two specs are equal iff every field
   matches — including the filter callable, which compares by identity
   for lambdas. `StripRegistry.register` uses `==` to detect duplicate
   registrations. If two import paths register the same strip with
   structurally identical but differently-instantiated filter lambdas,
   the second registration would raise. Stenota only registers once
   (at `stenota_graph` import time), so this shouldn't bite — but if
   tests register multiple times via re-import, watch out.

## Triage workflow

For each failing test:

1. **Read the failure carefully.** Most likely candidates above.
2. **Write a tighter failing test** next to the existing one if the
   bug shape isn't obvious.
3. **Fix the bug** in the source file.
4. **Re-run the entire affected test file** — not just the one test
   — to ensure you didn't break siblings.
5. **Commit each fix separately** with `fix: <short description>`.
6. **Don't squash with the original commit.** The previous Claude
   intentionally kept commits surgical; preserve that.

If you find a fundamental design bug (not a typo), STOP and write a
note in `HANDOFF-RESPONSE.md` explaining what's broken and proposing
fixes. Wait for the user. Don't unilaterally re-architect.

## Hard invariants — don't violate these while fixing bugs

From `TEMPORALITY-ROADMAP.md`. Brief form here for quick reference:

1. **Static-DAG execution path doesn't regress.** Existing 136
   temporal tests + every static-DAG test on `main` must pass.
2. **`stenota_lite/` stays nodecules-free.** It's a control group;
   no `import nodecules` allowed there.
3. **Schema coherence between lite and graph is semi-manual but
   maintained.** Don't introduce automated sync.
4. **Stenota's `Confidence`, `StructuredClaim`, `Mutability`,
   `AnnotationRef` in `stenota/core/models.py` are canonical.**
   Nodecules doesn't redefine these.
5. **`nodecules.core.time.TimeRange` and `stenota.core.models.TimeRange`
   stay structurally identical but distinct types.** Don't unify.
6. Time is integer milliseconds, meeting-relative. No floats.
7. `TimeSource` is injected, never global.
8. Cache-key stability for static deterministic nodes.
9. Core library works without DB/Redis.
10. No provider name branching in `core/` or orchestration.
11. Tortoise/hare workers (when they land) emit new derivation events,
    never overwrite.

The full list lives in `TEMPORALITY-ROADMAP.md` § "Hard invariants
across every phase."

## What's deferred — do NOT ship without consulting the user

Three follow-up PRs are intentionally not in this branch:

- **PR-n7b: scheduler v2 rewrite.** Dirty queue + hare/tortoise +
  phase-aware emission + cold-start vs resume signaling + pre-roll via
  history reach + `run_batch` → `run_batch_oneshot` rename. The
  scaffolding (cycle validator, settling_windows, DerivationPhase) is
  in; the actual scheduler rewrite is multi-week and needs stenota
  smoke runs to validate the IIR pre-roll math. Don't ship without
  the user.

- **PR-n9b: concrete tool-aware adapters.** Real Ollama/Anthropic/
  Bedrock implementations of `ToolAwareProvider`. Each requires the
  wire format validated against actual API responses. The abstraction
  is locked; concrete adapters slot in without interface churn. Don't
  ship without API access + canned response fixtures.

- **PR-s2b: stenota node migration + scheduler wiring.** Two coupled
  changes:
  1. nodecules: `TemporalScheduler.__init__` accepts
     `strips=`/`sidecar=`/`subscriptions=`/`environment=` kwargs and
     threads them into the `ChunkedContext` it constructs.
  2. stenota: `SummarizerL2Node` migrates from
     `iter_jsonl(claims_path, StructuredClaim)` + manual filter to
     `ctx.strip(STRIP_TURNS).in_range(window)`. `stenota_graph/cli.py`
     passes `strips=stenota_graph.STRIP_REGISTRY` to the scheduler.
  Hot-path behavior change; needs a meeting run to validate. Don't
  ship without the user.

## Files worth reading first

In this order:

1. `TEMPORALITY-ROADMAP.md` — architectural arc for everything that
   shipped + everything deferred.
2. `TEMPORALITY.md` — PR-n3 design baseline.
3. `CLAUDE.md` (nodecules) — invariants for changes.
4. `backend/nodecules/core/strips.py` + `subscriptions.py` +
   `environment.py` + `llm_providers.py` + `cycle_validator.py` — the
   new modules.
5. `backend/tests/temporal/test_strips.py` + `test_cache_eviction.py`
   + `test_subscriptions.py` + `test_environment.py` +
   `test_llm_providers.py` + `test_cycle_validator.py` +
   `test_is_deterministic.py` — the new tests.
6. The `claims/L2.jsonl` finalize pattern in
   `stenota_graph/nodes/finalize.py` — the existing strip-shaped
   pattern this work formalizes.

## One framing note

The previous Claude operated without ability to run tests; the work
is "intent shipped as code." Treat the design as load-bearing and the
implementation as best-effort. Be willing to break things and fix
them — the commit graph is set up for clean reverts if any specific
PR turns out wrong.

The user wants the system tested and the bugs surfaced, not
perfection. If something fails in a way you can fix in <30 minutes,
fix it. If it fails in a way that requires re-thinking the design,
write up findings in `HANDOFF-RESPONSE.md` and wait.

Good luck.
