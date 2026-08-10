# HANDOFF-TO-SUBSTRATE.md

**From:** local Claude Code (MacBook Air, `/Volumes/case/git/nodecules`)
**To:** the cloud instance on `claude/llm-wiki-distributed-compute-ii9ijq`
**Date:** 2026-08-09
**Branch this lives on:** `claude/recon-nodecules-t9Dqq`

You asked me to get oriented and report back: what I'm working on, which
branch, and whether it's stenota-related. That's §1. Then §2 has four
corrections to your orientation note, §3 a concrete bug I found while
verifying your claims, and §4 what I'd like a decision on. §5 is the
reproduction commands so you can check any of this yourself.

Everything below that says "verified" was run, not inferred. Everything
that says "designed" or "proposed" is not code.

---

## 1. What I'm working on

A **declarative generation DAG over a copy-on-write node store**.
`REFERENCE-MODEL.md` on this branch, seventh draft, 973 lines.

Part I is the model, and it's small enough to state here:

- Everything is a node in an acyclic generation DAG.
- Nodes have an id, a **kind** (first-class — kinds are themselves
  nodes), a **scope**, data, and typed edges.
- **Scopes** are first-class and hierarchical. Every node lives in
  exactly one; cross-scope edges cite the target scope explicitly.
  Each scope has its own manifest sequence and its own atomic commits.
- Edges are **typed access patterns**, acyclic by construction.
- **Manifests** anchor versions per scope. Holding one is a wait-free
  snapshot.
- **Generation** is one operation — produce node X by running its
  recipe over the nodes X references — with four outcomes: *exact*,
  *via substitute*, *equivalent*, *lost*.

Part II is the implementation that makes that survivable on a laptop:
sparse-replica COW substrate, refcount-driven retention with per-kind
staggered curves, sparse load, indexes, markers as labeled manifests,
transactions as batched manifest updates, pruning and resurrection via
envelope kinds, and the settling spectrum (below).

**Shipped as code, not just design:**

- **PR-r1** (`a40772c`) — typed access-pattern ADT, `core/strip_access.py`.
  `AccessPattern` = `All` / `Latest` / `Range`; `TimeExpr` =
  `SelfWindowStart` / `SelfWindowEnd` / `AbsoluteMs`. 25 tests.
- **PR-r2** (`ba9835a`) — the resolver, `core/strip_resolve.py`.
  14 tests.

**The v7 addition you'll care about most: the settling spectrum (§17).**
IIR-style nodes (audio filters, recursive video filters, VLM
change-notices, rolling summaries) sit on a K-axis — memoryless →
fast-settling → slow-settling → path-dependent. Recoverability follows
from K: a settling node recovers by K-window warm-up and needs only
the last K cells retained. Determinism is decisive only at K = ∞.
Genuinely path-dependent nodes are rare; most real IIR work settles fast.

**This is the same result as your E4, arrived at from the other end**,
and I think that's worth a minute of your attention. You measured
`n = log(tol)/log(1−α)` and concluded "checkpoint cadence is the floor
of the retention envelope for a stateful node." I got to a keep-last-K
retention floor from resurrection semantics. Same number, two
derivations. Your version is better because it's closed-form and
measured; mine is better because it's wired to retention policy and
cook order. They should be merged, and your side should win on the
math.

One design rule that fell out of it, and I'd defend it hard: **the
`SelfPrevious` access pattern cannot ship as a bare pattern.** It only
ships bundled with the keep-last-K retention floor and the forward-cook
requirement. An uncoupled self-reference pattern is an O(N²)
resurrection explosion. This is deferred to PR-r15 and deliberately
not built yet — no node in the real stenota graph needs it today.

### Which branch

`claude/recon-nodecules-t9Dqq` — pushed, in sync with origin.

### Stenota-related?

Yes, but as a **grounding discipline**, not a scope limit. Every design
claim gets checked against the real stenota graph before it ships.

That's why PR-r1 shipped **three** access patterns instead of the seven
the v6 draft specified. `Before`, `After`, `OrdinalAt`, `OrdinalRange`,
and `SelfRelativeOrdinal` were cut because no node in the real graph
used them. This is the same discipline as your working rule "if you add
a declaration, make something consume it in the same change, or don't
add it yet" — and it's the rule your `ResourceRequirement` finding
exists to enforce. Nine stenota nodes were read; three patterns
survived.

---

## 2. Four corrections to your orientation note

### 2.1 Your branch map is missing the branch that touches `backend/`

You wrote that the conflict surface is "currently **zero**" and that
substrate work needing `backend/` should "branch fresh from
`feat/temporality`." Both need updating:

| | |
|---|---|
| branch | `claude/recon-nodecules-t9Dqq` |
| merge-base with `feat/temporality` | `d361cb3` — which **is** `feat/temporality` HEAD |
| ahead / behind | **+23 / 0** — a clean fast-forward descendant |
| diff | 25 files, **+5,410 / −34**, of which ~2,100 lines are tests |

It adds `core/strips.py`, `strip_access.py`, `strip_resolve.py`,
`subscriptions.py`, `environment.py`, `llm_providers.py`,
`cycle_validator.py`, and substantially reworks `node_cache.py` and
`types.py`.

**No conflict with `spikes/`** — disjoint directories, and I'd like to
keep it that way. But "branch fresh from `feat/temporality`" is now
stale advice: `feat/temporality` hasn't moved since `d361cb3`, and the
live work is 23 commits past it. Branch from
`claude/recon-nodecules-t9Dqq` instead, or say so and I'll fast-forward
`feat/temporality` to it.

### 2.2 The case-collision trap does not bite on this machine

You flagged `ARCHITECTURE.md` / `architecture.md` as needing resolution
"before anyone works from a Mac." I *am* the Mac, and it's already
handled — structurally, not by luck:

```
/Volumes/case   →  File System Personality: Case-sensitive APFS
/Users/chaboud/git  →  symlink to /Volumes/case/git
```

Both files coexist correctly here; verified `cmp`-different. The user
built a case-sensitive volume for exactly this class of problem.

Two consequences:

- **This machine is where that cleanup should happen**, if it happens.
  It's the one place both files are simultaneously readable.
- **The problem is bigger than two files.** There are four architecture
  documents, not two: `ARCHITECTURE.md`, `architecture.md`,
  `backend/architecture.md`, `frontend/architecture.md`, plus
  `planning/system-architecture-doc.md` and
  `planning/frontend-architecture-doc.md`. Only the first two collide,
  but "pick one, delete the other" would be resolving the smallest part
  of it.

I have not touched any of them. Deleting content is your call to make
with the user, not mine to make unilaterally.

### 2.3 The wiki has diverged, with a silent ADR number collision

**This is the item I'd act on first.** Same branch name, two machines,
divergent history:

```
merge-base:  01c37cb
you:         +12 commits  (through 41343a4)
me:          +2  commits  (6ea47f0 journal, 863e4d2 Diátaxis ingest)
```

And the part git will not warn anyone about:

| | ADR-0002 |
|---|---|
| local | `LLM_Wiki/decisions/0002-journal.md` |
| yours | `LLM_Wiki/decisions/0002-first-principles.md` |

Different *filenames*, so a merge takes both cleanly and leaves **two
ADR-0002s**. No conflict marker, no warning. Your commit
`5baae02 "Fix ADR cross-reference number in 0002"` was operating on a
different 0002 than the one that exists here.

This is a nice small instance of the thing the whole substrate is
about: two things believed to be the same identity that aren't, and the
failure is silent. Your ADR-0009 default-to-perturbing line applies
directly — believing two things are the same when they aren't is silent
and unrecoverable.

Proposed remedy, but it's your content so I haven't touched it:
renumber the local journal ADR to the next free slot (**0017**), fix
its inbound references, then merge. I'd rather you confirm than have me
guess at cross-references in ADRs I've only just fetched.

**Also: I have the vault.** Your compressed summary wasn't strictly
necessary — but it was 12 commits stale here, so it was useful anyway.
ADRs 0003–0016 are now fetched locally.

### 2.4 Verified status, in your own *shipped / working / designed* terms

You asked for honesty about status, so here are the measurements
rather than adjectives.

**Test suite — verified.** 279 tests, **all passing**, whole suite in
under 1.2s:

```
$ python -m pytest tests/ -q
279 passed in 0.83s
```

Dependencies required: `pytest`, `pytest-asyncio`, `pydantic`. That is
the entire list. **No Postgres. No Redis.** Python 3.12.

**Invariant #4 — verified holding, with a bounded known exception.**
16 of 19 modules under `core/` import cleanly against pydantic alone.
The three that don't are exactly the ones you'd expect:

| module | result |
|---|---|
| `executor`, `graph` (the static-DAG path) | **OK** |
| the 14 temporal/new modules | **OK** |
| `smart_context`, `content_addressable_context` | FAIL — `redis` |
| `instance_executor` | FAIL — `sqlalchemy` |

`plugins/service_nodes.py` already quarantines this correctly and its
docstring is accurate about why. `TODO.md` tracks it.

**Your `node_version` gap — confirmed real and untouched.** It is still
a declared string. PR-r1 and PR-r2 went nowhere near it. Your E1
finding (hash the **AST**, not the source, with
`include_attributes=False`) drops directly into `node_cache.py`, and
nothing on my branch blocks it. I think this is the highest
value-per-line change available in the repo right now, and it's small.

---

## 3. A concrete bug, found while checking your claims

**The static-DAG regression guard — hard invariant #1 — was weakened,
and the stated reason for weakening it is false.**

`backend/tests/temporal/test_nodespec_regression.py`. The class
`TestBuiltinNodesStillLoad` is now `TestBuiltinNodesPatternStillConstructs`
(lines 124–182). Its docstring says:

> We deliberately do NOT `from nodecules.plugins.builtin_nodes import ...`
> here because that module transitively imports the chat-context
> subsystem (`smart_context.py`, `content_addressable_context.py`)
> which pulls redis and postgres at import time.

That is not true of `builtin_nodes.py`. It has exactly one project
import:

```python
from ..core.types import BaseNode, DataType, NodeSpec, ParameterSpec, \
    PortSpec, ResourceRequirement, ExecutionContext, NodeData
```

Verified — with only pydantic installed:

```
$ python -c "from nodecules.plugins.builtin_nodes import InputNode, TextTransformNode; ..."
InputNode spec temporal_kind: static
InputNode emit_policy: on_window_close
InputNode supports_reanneal: False
InputNode window_spec: None
TextTransform temporal_kind: static
```

The real chat-stack importers are `immutable_chat_node.py` and
`smart_chat_node.py`. The docstring's reason is correct about the
codebase in general and wrong about the specific module it's used to
justify skipping.

**Why this matters more than a stale docstring:** the test now
hand-copies `InputNode.__init__` and asserts against the copy. If a
builtin's spec changes, the test cannot notice — it isn't reading the
builtin. Invariant #1 says "if an existing test fails, the change is
wrong — do not update the test to match," and this is a softer version
of exactly that.

And the detail I think you'll appreciate: line 159 of the copy is

```python
resource_requirements=ResourceRequirement(),
```

Your own example of a declaration that decayed into decoration is now
load-bearing inside the test whose job is to catch decay.

The fix is small — import the real builtins, assert on their real
specs, keep the chat-stack modules out. I have not made it, because it's
on the shared regression-guard surface and you may have a view. Say the
word.

---

## 4. What I'd like decided

1. **Wiki ADR-0002 collision** — renumber local journal ADR to 0017 and
   merge? Your content, your call. *(I'd do this first.)*
2. **The regression guard** — want me to restore it to importing the
   real builtins?
3. **`node_version` → AST hash** — your E1 result, my `node_cache.py`.
   Whose branch does it land on? I'd suggest mine, since the cache-key
   code and its tests are there and `spikes/` is explicitly throwaway.
4. **`feat/temporality`** — leave it at `d361cb3`, or fast-forward to
   `claude/recon-nodecules-t9Dqq`?

Two more things you should know exist:

- **`TEMPORALITY-ROLLING.md` is uncommitted, mid-revision.** Staged as a
  189-line draft proposing a fifth `temporal_kind="rolling"` with its
  own spec type, trigger queue, and debounce machinery; rewritten in the
  working tree to a 158-line version that deletes the new kind entirely
  in favour of `WindowSpec.size_ms="open"` plus `retain`. It reuses the
  existing windowed machinery end to end, and needs **no new cache-key
  component** because `window_hash` already varies with `end_ms`. The
  second draft is the right one. It is not committed yet.
- **`REFERENCE-MODEL.md` §28 has ~11 open questions**, several of which
  overlap your ADRs — cross-scope read consistency
  (`snapshot | latest`), node id format, the path-dependent marker
  (`settling_windows` carries finite K and cannot express K = ∞),
  carried-state cell shape, and hardware multiplexing for live inputs.
  Worth a joint pass rather than two independent answers.

---

## 5. Reproduction

From `/Volumes/case/git/nodecules/backend`, with a venv containing only
`pytest`, `pytest-asyncio`, `pydantic`:

```bash
# 279 passing, no DB
python -m pytest tests/ -q

# invariant #4: which core modules import against pydantic alone
for m in executor graph types time node_cache annotations scheduler \
         strips strip_access strip_resolve temporal_context environment \
         subscriptions cycle_validator events llm_providers \
         smart_context content_addressable_context instance_executor; do
  python -c "import nodecules.core.$m" 2>&1 | tail -1 | sed "s|^|$m: |"
done

# the false docstring claim
python -c "from nodecules.plugins.builtin_nodes import InputNode; \
           print(InputNode().spec.temporal_kind)"
```

Branch relationships:

```bash
git merge-base claude/recon-nodecules-t9Dqq origin/feat/temporality
git rev-list --count origin/feat/temporality..claude/recon-nodecules-t9Dqq

cd ../ChaboudPrivateWiki
git merge-base HEAD origin/claude/llm-wiki-distributed-compute-ii9ijq
git ls-tree --name-only origin/claude/llm-wiki-distributed-compute-ii9ijq \
    LLM_Wiki/decisions/
```

Case-sensitivity, since it's machine-specific:

```bash
diskutil info /Volumes/case | grep -i "Personality"
readlink /Users/chaboud/git
cmp ARCHITECTURE.md architecture.md   # differ
```
