# Open-window recook — design memo

**Status:** design draft, 2026-06-02; **grounded against the code
2026-08-11**. An additive refinement of the existing
`temporal_kind="windowed"` semantics on the `feat/temporality` branch.
Companion to `TEMPORALITY.md`. **No implementation yet** — nothing in
this memo is shipped, and the "Grounding" section marks which of its
claims about *existing* code are verified and which were wrong.

**Earlier draft** of this memo proposed a 5th `temporal_kind="rolling"`
with a separate `RollingSpec`, trigger queue, debounce machinery, and
scheduler hook. That draft was overdesigned. The user pushed back with
a much simpler observation: this is just a window with **a start time
but not an end time** plus **retain + recook** semantics. The simpler
design reuses the existing `windowed` infrastructure end-to-end.

## Motivation

Today nodecules supports four temporal kinds: `static`, `windowed`,
`streaming`, `reanneal`. Combined with three emit policies they cover
most cases. A class still doesn't fit:

- A **live "claims processed" counter** that updates as upstream emits
- A **streaming TOC** that grows an entry per L3b arc closing
- An **incremental L4 snapshot** that re-cooks every M minutes
- A **growing render** that re-writes `meeting.md` mid-meeting for
  interactive review
- A **mid-meeting save point** every K windows

What's common: the node has a **time region with a fixed start and a
moving end**, the node emits a **derived state** from accumulated
content in that region, and the node **re-cooks** as the region grows.
The output is **retained** between re-cooks so downstream consumers can
read "the current state" without waiting.

## The proposal

No new `temporal_kind`. Instead, extend `WindowSpec` so the existing
`windowed` kind can express open windows:

```python
@dataclass                                    # NB: a dataclass, not a Pydantic
class WindowSpec:                             # model — see "Grounding" below
    size_ms: int | Literal["open"]            # NEW: "open" = end_ms is dynamic
    stride_ms: int                            # for "open": the recook cadence
    align: WindowAlignment = "origin"
    min_upstream_coverage: float = 1.0
    retain: bool = False                      # NEW: keep the most recent emission
                                              # visible at the output port between
                                              # recooks (default False = each emission
                                              # is captured then discarded)
```

`size_ms="open"` semantics:

- **The window starts at the node's first run time** (or, more usefully,
  at meeting t=0 — the alignment field decides).
- **The window's end_ms is the scheduler's current "now"** at each
  re-cook — which is the FileClock advance point for batch, the
  WallClock for live, or whatever the TimeSource reports.
- **stride_ms decides the recook cadence.** Every `stride_ms` of clock
  advance, the scheduler enqueues a recook of this node. Reused machinery
  — same way fixed windows are enqueued today.
- **min_upstream_coverage would apply** — a recook only fires if the
  accumulated upstream coverage over `[start, now]` passes the threshold.
  This is how a node says "don't recook unless meaningful new input has
  landed." **But this mechanism does not exist yet.** See "Grounding"
  below: `min_upstream_coverage` is declared, validated, and enforced by
  nothing. Until it is implemented, `stride_ms` is the *only* gate, and
  an open-windowed node will recook on cadence whether or not new input
  landed. This memo must not be read as though the coverage gate were
  live.
- **retain=True** keeps the most recent emission live at the output
  port between recooks. Downstream `static` consumers always see the
  latest value. Without retain, the output port only briefly holds the
  emission then clears, matching the existing per-window semantics —
  useful for nodes that emit events rather than maintain state.

## Why this is so much smaller than the previous draft

| previous draft | this draft |
|---|---|
| new `temporal_kind="rolling"` (5th kind) | reuse `temporal_kind="windowed"` |
| new `RollingSpec` with 4 trigger enum + debounce knobs | extend `WindowSpec` by 1 field (`size_ms="open"`) + 1 field (`retain`) |
| new trigger queue in the scheduler | reuse the existing window enumeration loop |
| new cache key component `progress_hash` | existing `window_hash` already varies with `end_ms` — caches naturally distinguish each recook |
| 4 trigger types to implement and test | 1 trigger type — clock-advance crossing stride_ms |
| open question on debounce/coalesce | `stride_ms` IS the debounce |

## Grounding against the code

Every claim above was checked against the code on
`claude/recon-nodecules-t9Dqq` rather than argued. Verified 2026-08-11.

| claim | verdict | evidence |
|---|---|---|
| four temporal kinds, three emit policies | **true** | `types.py:58-59` |
| `window_hash` already varies with `end_ms`, so no new cache-key component | **true** | `hash_window` canonical-JSONs a `TimeRange`, whose fields are `start_ms` + `end_ms` (`node_cache.py:77-81`, `time.py:29-30`) |
| `retain` consolidation is a one-line branch | **true** | `scheduler.py:329-330` — `values` becomes `values[-1]` |
| `compute_windows` is the single change point | **true**, and better than claimed | `size_ms` has exactly **one** use site outside `types.py`: `scheduler.py:110` |
| `emit_policy="on_graph_close"` is real, so the `RenderMarkdownNode` diagnosis holds | **true** | `scheduler.py:232-236`, `258-265` |
| `WindowSpec(BaseModel)` | **wrong** | it is a `@dataclass` (`types.py:64-65`). `TimeRange` *is* Pydantic; `WindowSpec` is not. Fixed above. |

Three things the earlier draft missed, all found by reading rather than
by reasoning:

**1. `min_upstream_coverage` is enforced by nothing.** It is declared on
`WindowSpec`, range-validated in `__post_init__`, and has a test — for
the *validation*, not the behaviour. `compute_windows`'s own docstring
says "we trust `min_upstream_coverage` to be enforced by the scheduler
at dispatch time, not here" (`scheduler.py:92`) and the scheduler never
enforces it. Grepping the whole backend finds the declaration, the
validator, and two tests of the validator. No consumer.

This is the second instance in this repo of the same failure mode as
`ResourceRequirement` on `main` — a declaration nothing consumes decays
into decoration. It matters here specifically because the "don't recook
unless meaningful new input landed" story is the memo's answer to
debounce, and it has no mechanism behind it. Implementing the coverage
gate is a prerequisite for open windows, not a nice-to-have alongside
them.

**2. `size_ms="open"` dies with a confusing error today.** The
`__post_init__` guard is `if self.size_ms <= 0: raise ValueError(...)`,
and `"open" <= 0` raises `TypeError: '<=' not supported between
instances of 'str' and 'int'` — a type error from a validator, not a
validation error. The validator has to branch on `size_ms == "open"`
*first*. Small, but it is a real edit and the earlier draft implied
the field change was free.

**3. `retain=True` changes the type a downstream node sees.** Today a
windowed node's consolidated output is a **list** of per-window values
(`scheduler.py:323-330`). With `retain=True` it becomes a **scalar**.
That is a port's observable type changing based on a flag on the
producer — which is exactly the flag-driven-policy smell
`REFERENCE-MODEL.md` §29 cites against (`structural_over_policy.md`),
and it is not additive in the way the rest of this design is. Two
honest options: accept it and document `retain` as part of the port
contract, or express "latest" structurally as a separate output port so
both shapes are always available. **Unresolved — this is the one open
design question left in this memo.**

Also worth recording, because it changes the size of the
`compute_windows` edit: the existing loop enumerates window **starts**
at a fixed size (`scheduler.py:96-110`). An open window is the
transpose — a fixed start with enumerated **ends**. So it is a genuine
second branch in that function, not a tweak to the existing one. Still
small; still one function.

## Observability becomes "pick points"

The user's framing for what this enables: as the meeting cooks, every
recook of an open-windowed node is a **pick point** — a moment with a
fully-materialized output you can read. The cinnamon-roll observability
surface scrolls through pick points. A reviewer scrubbing back through
the meeting can land on any pick point and see what the system knew at
that instant. There's no separate observability machinery — the cache
already stores every emission keyed by `(node_id, window_hash)`, and
each recook produces a new `window_hash`. The "history of pick points"
is just the cache view filtered to this node.

## Cache invariants preserved

- Static nodes' cache keys: unchanged.
- Fixed `windowed` nodes' cache keys: unchanged.
- Open `windowed` nodes' cache keys: vary by `end_ms` (already part of
  `window_hash`). Each recook is a distinct cache entry, naturally.

No migration. No new key component.

## Scheduler hook

The existing windowed pass does:

```python
windows = compute_windows(spec.window_spec, total_range)
for window in windows:
    await self._execute_window(...)
```

For an open window spec, `compute_windows` returns a sequence of
`[t0, t0+stride]`, `[t0, t0+2*stride]`, `[t0, t0+3*stride]`, ... up to
`total_range.end_ms`. Same downstream code path. Same `_execute_window`.
The only change is `compute_windows` knowing what to do when
`size_ms == "open"` — but note it is a *second branch* in that function,
not a tweak to the existing one, because the existing loop enumerates
starts at fixed size and an open window enumerates ends at a fixed
start.

Two edge cases the implementation has to answer, both inherited from
the existing code rather than introduced here:

- **`TimeRange` rejects zero duration** (`end_ms <= start_ms` raises,
  `time.py:34-40`). The first open window `[t0, t0+stride]` is fine, but
  an implementation that clamps ends to `total_range.end_ms` must not
  emit `[t0, t0]` when `t0 == total_range.end_ms`.
- **The last window currently may extend past `total_range.end_ms`** —
  `compute_windows` says so explicitly and leaves clamping to callers
  (`scheduler.py:93-94`). For open windows the final recook should
  probably clamp exactly, so the last pick point describes the real
  extent of the data rather than a range running past it. That is a
  behaviour choice, and it should be made deliberately rather than
  inherited by accident.

For `retain=True`, the consolidation step at the end of the windowed
pass takes the **last** per-window output instead of building a list —
`values` becomes `values[-1]` at `scheduler.py:329-330`. That much is
genuinely a one-line branch. What it is *not* is free: see Grounding
finding 3 on the port-type change.

## When NOT to use this

- **Fixed-resolution rolling summaries** (L3a 5-min, L3b 15-min):
  these are naturally `size_ms=300000, stride_ms=300000` windowed — no
  "open" needed. Each L3a window is a self-contained 5-min slice.
- **One-shot finalizers** (render of L2 at L2-close, write manifest):
  `static + emit_policy=on_window_close` (the default). The previous
  `on_graph_close` misuse on stenota's `RenderMarkdownNode` was this
  case — not a rolling node.
- **Per-token streaming output**: `temporal_kind="streaming"`. Token
  cadence is finer than any sensible `stride_ms`.

## What lands when

1. **This memo** — the design.
2. **Resolve the `retain` port-type question** (Grounding finding 3).
   Flag-driven port semantics or a separate "latest" port. Nothing else
   here should land before this is decided, because it determines what
   `retain` even means at the port contract.
3. **Enforce `min_upstream_coverage`** (Grounding finding 1). This is a
   prerequisite, not a companion: it is the coverage gate the whole
   recook story depends on, it is already declared and validated, and
   it currently does nothing. It is also worth landing on its own merits
   — fixed windows have been silently ignoring it since it was
   introduced, which is a live correctness gap independent of open
   windows. Ships with the test that would have caught it.
4. **`size_ms="open"` and `retain` on WindowSpec.** Additive — defaults
   keep existing nodes unchanged. The `__post_init__` guard has to
   branch on `"open"` before the numeric comparison (Grounding
   finding 2). New tests under `backend/tests/temporal/` for open-window
   cache shape + retain consolidation.
5. **`compute_windows` extension** to enumerate `[t0, t0+stride*n]`
   sequences when `size_ms="open"`, with the clamping behaviour chosen
   deliberately.
6. **First real consumer** — a "claims processed" counter for the
   cinnamon-roll observability surface, an interactive review render,
   or an incremental L4. We do NOT land this without at least one
   consumer in the same PR.

Note that (3) reorders the work relative to the earlier draft. The
coverage gate was assumed to exist and turned out not to, which moved
it from "already handled" to "on the critical path."

## What this is NOT a solution to

The substrate bug that surfaced this discussion
(`commission_january e2b` cook, L3a/L3b empty) was a misuse of
`emit_policy="on_graph_close"` on stenota's `RenderMarkdownNode`. That
node IS a one-shot static finalizer — its correct emit_policy is the
default, so the scheduler runs it inline between L2 (done) and L3a
(next). The substrate bug fix is on the stenota side (one-line revert).
This memo describes a *future* primitive for an unrelated class of
nodes that genuinely want to emit during execution.
