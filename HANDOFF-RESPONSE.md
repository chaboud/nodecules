# HANDOFF-RESPONSE — verification pass on PR-n4..n9 + PR-n7 scaffold

Local-Claude pass on `claude/recon-nodecules-t9Dqq`. Followed the
HANDOFF triage workflow: read, run, fix, surgical commits.

## tl;dr

- **nodecules:** 240/240 temporal tests pass (was 237/240). 3 failures
  fixed across 3 surgical commits.
- **mypy:** branch is back to baseline (77 pre-existing errors in
  pre-temporality code; the 2 new errors PR-n4/n9 introduced are gone).
- **Three PRs deferred per HANDOFF (n7b, n9b, s2b) untouched.** Did not
  ship anything in those directions.
- **One design question raised** (cache eviction model) — see below.
  Surgical fix shipped; deeper rethink left to the user.

## Commits added on top of `6ab7fac`

```
b77d563 fix: mypy regressions in strips.py and node_cache.py
a8d158b fix: ToolSchema is hashable — exclude parameters from compare/hash
8b1c5cd fix: cache eviction must not drop the just-added entry
```

Each commit message documents its own bug shape and rationale; commits
are clean reverts if any specific fix turns out wrong.

## Bug shape per fix

### 1. `8b1c5cd` — cache eviction off-by-one

`InMemoryNodeCache._evict_if_needed` and the matching filesystem path
walked entries oldest-first looking for a deterministic victim. When
two pinned-nondeterministic entries already filled a cap=2 cache and
you `put()` two deterministic entries in succession, the second put
immediately evicted itself:

```
cap=2, store=[llm0,llm1]   (both pinned)
put(det0) → store=[llm0,llm1,det0], len>cap
   → find oldest det → det0 → evict → store=[llm0,llm1]
put(det1) → store=[llm0,llm1,det1], len>cap
   → find oldest det → det1 → evict → store=[llm0,llm1]  ← bug
```

Fix: thread the just-written digest (or path) through the eviction
function and skip it while searching for a victim. Restores the
contract callers expect of `put()` — the value is in the cache when
the call returns.

Behavior under "cap exceeded, only pinned entries exist" is now: cache
holds `n_pinned + 1` entries (the just-added survives), and subsequent
puts each replace at most their own deterministic predecessor. Matches
the existing `test_all_nondeterministic_cap_exceeded_is_safe` "safe
failure mode" docstring.

### 2. `a8d158b` — `ToolSchema` unhashable

`@dataclass(frozen=True)` auto-generates `__hash__` from all comparable
fields, but `parameters: Dict[str, Any]` is unhashable. The test
`test_tool_schema_is_hashable` documents the intent: equality reduces
to name+description "at the dataclass level." Marked `parameters` as
`field(compare=False)` — matches the documented contract, makes
`ToolSchema` set-addable for de-dup at the agent layer.

If you ever want structural equality on the JSON-Schema dict itself
(e.g., agent caching that distinguishes tool-name `foo` with two
different parameter shapes), this is the wrong call and we'll need a
custom `__hash__` over canonical-JSON of `parameters`. Flagging.

### 3. `b77d563` — two new mypy errors

- `strips.py:91` — `schema_cls: type` hid `model_validate_json` from
  mypy. Retyped as `type[StripSchema]` (the existing duck-typed
  Protocol). Preserves duck-typing intent; lets mypy check call sites.
- `node_cache.py:183` — `OrderedDict[str, dict]` missing inner type
  args. Spelled as `OrderedDict[str, dict[str, Any]]`.

These were the **only two new mypy errors** PR-n4..n9 introduced.
Baseline (`feat/temporality`) has 77 pre-existing mypy errors that
pre-date this branch; not touched.

## Design question — cache eviction model

While fixing #1 above, you flagged that LRU-with-pinning may be the
wrong abstraction. Capturing the alternative here as a design question
to revisit, not as work to do now.

**What's there now (LRU + opt-in eviction):**

- `InMemoryNodeCache(max_entries=N)` and `FilesystemNodeCache(...,
  max_size_bytes=, max_entries=)`.
- Each entry has an `is_deterministic` bool. Deterministic = evictable
  on cache pressure (recomputable). Nondeterministic = pinned (LLM /
  stochastic outputs).
- The cache stores **copies** (`_prepare_for_storage` deep-copies dicts
  and `.model_dump()`s Pydantic models). It's not a shared-reference
  store — downstream consumers get values via the executor's edge
  wiring, not by holding refs into the cache.
- Cache miss = recompute. The cache is an opt-in optimization.

**The reference-tree / CoW alternative:**

- Each cache entry knows its consumers (downstream `(node, window)`
  pairs that still need it).
- Eviction = "drop entries with zero live consumers among nodes that
  are recomputable." No fixed cap.
- "Cap exceeded but can't shrink" failure mode disappears: pinned
  entries naturally drop when no consumer references them, even if
  they're nondeterministic, because nothing can ask for them anymore.
- Composes with the eventual PR-n7b dirty-queue scheduler: the
  scheduler already knows which `(node, window)` pairs are pending /
  blocked / done, so it's the right place to maintain reference counts.

**Why not now:**

- Requires scheduler ↔ cache liveness signal (significant API
  addition). PR-n7b is the natural home for that change.
- Doesn't fix any current symptom; LRU-with-pinning is correct for
  single-graph, single-machine runs in the current scope.
- Existing tests (`test_nondeterministic_entries_pinned`,
  `test_all_nondeterministic_cap_exceeded_is_safe`) encode LRU
  semantics; a model change would rewrite those.

**Suggestion:** revisit when designing PR-n7b. The dirty-queue + hare/
tortoise design already touches the scheduler's per-`(node, window)`
state; adding a `cache.retain(key)` / `cache.release(key)` call from
the scheduler at execution boundaries is the natural extension. The
filesystem backend can keep an LRU fallback for warm-restart cases.

## Things deliberately NOT done

- **PR-n7b** (scheduler-v2 rewrite). HANDOFF explicit. Not touched.
- **PR-n9b** (concrete Ollama/Anthropic/Bedrock adapters). HANDOFF
  explicit. Not touched.
- **PR-s2b** (stenota node migration to `ctx.strip(...)` + scheduler
  kwarg threading). HANDOFF explicit. Not touched. Verified
  `stenota_graph/strips.py` registers strips at import time but
  nothing in `stenota_graph/nodes/` reads them yet — matches the
  HANDOFF's "registered but unused" framing.
- **Pre-existing mypy errors** in `executor.py`, `scheduler.py`,
  `instance_executor.py`, `smart_context.py`, etc. These pre-date
  this branch (see `feat/temporality` baseline). Not in scope.

## Likely-bug list (HANDOFF section 5) — outcome

1. **Async test discovery** — non-issue. `pyproject.toml` line 68 has
   `asyncio_mode = "auto"`. All async tests collected and run.
2. **Import path drift** — non-issue. `tests/conftest.py` puts
   `backend/` on `sys.path`; `from nodecules.core.X` works without
   `poetry install`.
3. **`StripView.before()` early termination** — not triggered. Tests
   pass with in-order JSONL. The pathological out-of-order JSONL
   concern remains valid as future-proofing, but no current consumer
   produces such files.
4. **`Subscription._sentinel` identity check** — works fine.
5. **`Visibility` default phases** — works fine.
6. **`StripCycleError.__init__` ordering** — works fine.
7. **Pydantic v2 method usage** — Pydantic v2.12.3 is what `pip
   install pydantic` resolves; `model_validate_json` /
   `model_dump_json` / `ConfigDict(frozen=True)` all work.
8. **`StripSpec` equality with Callable filter** — works fine in the
   current single-registration pattern. The repeat-import concern is
   theoretical; no test exercises it.

The actual three failures were not on this list. Worth knowing for
calibration: cache eviction + dataclass-hash issues are the kind of
thing best caught by "did you actually run pytest" rather than careful
reading.

## Stenota verification

`pytest tests/test_strips.py -v` — **7/7 pass after fix** (the HANDOFF
said 11 cases; the file actually has 7, minor doc drift).

One failure surfaced: `test_idempotent_registration`. This is HANDOFF
predicted-bug #8 hitting in practice. `register_stenota_strips()`
defined the turns filter as `filter_fn=lambda c: c.kind == "turn"`
inside the function body, so each call instantiated a fresh lambda.
`StripSpec` is a frozen dataclass whose `__eq__` compares all fields
structurally, and lambdas compare by identity — so the second
registration of `strips/turns/diarized` got a different filter_fn and
the registry's "spec must match for re-registration" check raised.

Fix (stenota commit `bfcb2c4`): hoisted the turn filter to a
module-level `_is_turn` function so its identity is stable across
calls. Matches the function's documented "idempotent registration"
intent.

The deeper question — should `StripRegistry.register` itself be more
lenient about filter_fn identity? — is left open. The current
behavior is a reasonable safety check; the brittle part is library
authors writing lambdas in declarative specs. Documenting the pattern
("strip filters must be module-level functions, not lambdas") in
`nodecules/core/strips.py` docstring would prevent the next instance.
Not done in this pass to keep the change surgical.

## Final state

- nodecules: 240/240 temporal tests pass; mypy back to baseline; 3
  surgical fix commits + this response doc.
- stenota: 7/7 strip tests pass; 1 surgical fix commit.
- All 8 commits unsquashed and revertable individually.
- HANDOFF's three deferred PRs (n7b, n9b, s2b) intentionally untouched.
