# nodecules: status

Where the build stands, measured against what it is supposed to be. Updated
2026-09-18 at the head of `claude/nodecules-v2-naming-matching-vmkexv`.
`WHY-WHAT-HOW.md` explains the design; this file says how much of it exists.

Status words: **built** means code with tests on the branch; **partial**
means some of the property exists and the rest does not; **designed** means
a written decision and no code; **not started** means that.

## The properties, one by one

The question asked on 2026-09-16 was whether we have "our universal,
substitution-friendly, abstract representation, distributable, async, high
performance, LLM integrated, agentic, multi-user, robust, extensible node
system." Each word, honestly:

| Property | Status | What exists | What does not |
|---|---|---|---|
| Universal (one node model for everything) | partial | One node shape for data, recipes, params, envelopes, manifests, timelines, frames, participants, attention. Everything the substrate itself produces is a node. | Kinds are bare strings with no registry or schemas. Descriptions, claims, hallmarks, executors, plans, and observations are still Pydantic objects outside the store, not store kinds. |
| Substitution-friendly | built at the decision level, partial in execution | Two-layer identity. Descriptions with a reference realization. The two-part satisfies judgment, measured on a bench where the impostor was the cheapest candidate. Receipts with an identity axis and a reproducibility axis. A plan's bindings substitute a realization and the receipt says `via-substitute`. | Routing kinds that substitute an upstream automatically. Substitution across machines. An audition against real model weights. |
| Abstract representation | built, with a known gap | Declarations are kind plus edges with symbolic access patterns; resolution happens at production time; what was read goes in the receipt, never back into the declaration. Graphs are plain JSON-able nodes. | The relative and range-relative patterns ("the entry before mine", "the last five minutes") were cut in PR-r1 and have not been restored. No ordinal axis for strips indexed by count. |
| Distributable | partial | Placement decides where each node runs and the plan is an artifact. Replicas commit locally and converge hash for hash. Residency is separate from identity, with a disk tier. A production that cannot run here becomes a request node in the store that any agent with the realization fulfils, so work moves between machines through a shared directory or a synced replica with no transport of its own. A store directory is git-mergeable: one file per candidate head, merged on attach, so two machines' checkouts are a transport today (`handoff/`). | No transport of the substrate's own. A plan does not run on the executor it names. No remote replica tier. |
| Async | partial | Realizations are async functions and production is an async call. | Production is pull-only and sequential: independent nodes do not cook concurrently, and nothing recooks when an input changes without being asked. The scheduler on the temporal branch is single-threaded by design. |
| High performance | not established | In-process numbers on the store from one container: about 7,900 single-node commits per second, 4.6 µs per read through a manifest, a 10,000-deep chain hashed in 186 ms, sixty ticks of ten participants' updates in 17 ms of a second. | No profiling of production. Pure Python with pydantic on the hot path. No disk. The numbers say the store is not the bottleneck for a demo; they say nothing about real load. |
| LLM integrated | partial | A single adapter turns any tool-aware provider into a realization, with unseeded calls marked non-reproducible; a node with no chat in front of it is put to the model as its inputs rendered by role. The chat demo runs a graph through it. A step that needs a model this process lacks is deferred as a request node, and `handoff/fulfil.py` is the worker that answers it from a machine with a model. A standard-library provider over any OpenAI-compatible endpoint exists. | The OpenAI-compatible provider has met only a fake server; the first real model behind it is the Spark session's (`handoff/SPARK.md`). No tool-use loop. No test has touched a real model. |
| Agentic | enabled, not built | Everything an agent would change is data it can commit: recipes, params, elements, its own attention. The chat demo edits a persona by commit. Agents are participants at the table. A decision is a node, its presentations are produced for a surface and a person, and the business graph advances exactly once when the choice is final. Pending requests are the handoff between agents. | No agent runtime: no loop from model output to tool calls to commits. No write grants; the self-modification carve-out is a principle in the vault, not code. No responsible adult. |
| Multi-user | built in-process | Authors on manifests. Three resolution policies for concurrent writes. Conflicts name base, ours, and theirs. Rebase and blame. Replicas that converge in either sync order. Participants, attention with expiry, optional following, field-wise merge of concurrent edits. Two demos. | No network. No access control or per-region grants. No responsible adult. No head-acceptance policy beyond convergence. No signed manifests. |
| Robust | partial: durable and failure-tolerant, not yet self-limiting | 482 tests on pydantic and pytest alone, running in about four seconds. Convergence is tested as a property. A realization that lies about determinism is flagged. The store persists to disk with write-through before the head moves, loads sparsely, and refuses tampered files. A failing realization is a state with an error, not a crash, and retry is a parameter. Three real bugs found by tests on the way (a recursion limit, a deadlock, a stale-body read) and fixed the same day. | Nothing is ever pruned by policy, so the store grows without bound. No concurrency tests, no fuzzing. |
| Extensible | built | Kinds are open. Merge rules, assay metrics, and realizations register by name. Access patterns are a discriminated union that admits new members additively. Policies are data on the manifest. Routing is graph structure: a router node with optional edges and a realization that reports its choice. | No kind registry with schemas, so a presenter cannot ask what kinds it can render. No discovery for realizations; the inventory is assembled by hand. |

The one-line version: the representation, the identity model, the
multi-writer model, durability, activation, routing, and tracking are built
and tested in one process, and an inspector shows and edits all of it;
distribution, execution at scale, real models, and agents are the parts
that are designed or modelled but not performed.

## What has been done

Everything below is on the branch with tests. Dates are when the slice
landed. Line counts are for the new core modules, about 4,700 lines in
total, plus the tests.

| Slice | Module | Tests | Landed |
|---|---|---|---|
| Typed access patterns and their resolver | `strip_access.py`, `strip_resolve.py` | 39 | before 2026-08-29 |
| Descriptions and the satisfies judgment | `descriptions.py`, `assay_metrics.py` | 53 | 2026-08-29 |
| Placement, cost model, Pareto front, observations | `placement.py` | 36 | 2026-08-29 |
| The store: persistent map, manifests, transactions, envelopes, residency | `pmap.py`, `store.py` | 48 | 2026-09-05 |
| Timelines, timebases, skew maps | `timeline.py` | 10 | 2026-09-06 |
| Resolution policies, clean-house flow, blame | `store.py` | (in the 48) | 2026-09-06 to 07 |
| Generation: produce a node, four outcomes, receipts, dispatch | `generation.py` | 9 | 2026-09-07 |
| The CRDT basis: manifest DAG, deterministic merge, sync | `replica.py` | 9 | 2026-09-13 |
| Scenes: presentation time, expiry, the frame, the simultaneity bound, transitions | `scene.py` | 8 | 2026-09-13 |
| Strips as nodes; a provider as a realization | `strip_nodes.py`, `llm_realization.py` | 2 | 2026-09-13 |
| The table: participants, attention, following, field-wise merge | `table.py` | 4 | 2026-09-15 |
| Persistence: a disk tier, sparse load, write-through, integrity | `disk.py`, `store.py` | 7 | 2026-09-16 |
| Activation: markers name versions; activating is a forward commit | `activation.py` | 2 | 2026-09-16 |
| Routing as graph structure, failure as a state, retry, tracking, lineage | `generation.py`, `tracking.py` | 4 | 2026-09-16 |
| The inspector: look, edit, produce, activate, watch | `demos/inspector/` | (smoke) | 2026-09-17 |
| Decisions and presentations; deferral of a step as a request node | `decisions.py`, `deferral.py`, `generation.py` | 4 | 2026-09-18 |
| The hand-off: a git-mergeable store, an OpenAI-compatible provider, a worker that fulfils requests, passed notes | `disk.py`, `store.py`, `openai_compatible.py`, `handoff/` | 5 | 2026-09-18 |

Also on the branch: three measured benches under `spikes/` (identity,
matching, placement), four running demos under `demos/` including the
inspector and the table chat, the spec (`REFERENCE-MODEL.md`), and
`WHY-WHAT-HOW.md`. Thirty-three decision records in the vault, two of
them superseded.

Two things were built and withdrawn the same day, and are recorded as
such: a generation pin on edges (it made rigidity spread through the
graph) and ownership leases (contention makes people wait).

## What is left

Grouped by what it unlocks. Nothing here has code unless it says so.

**Durability.** Retention policy and the pruning it drives, including
tombstones for deleted names across replicas. The disk tier exists; the
policy that decides what to release does not, so the store grows without
bound.

**Execution.** Concurrent production of independent nodes. Recomputation
driven by change rather than by request. Running a plan on the executor it
names.

**Distribution.** A transport for replica sync. The remote replica tier.
Executing across a process boundary. Head acceptance as a field, with the
models the vault taxonomy names.

**Kinds.** A registry with schemas, so a presenter can say what it can
render and a valence check can be typed. Descriptions, claims, hallmarks,
executors, plans, and observations as store kinds.

**Representation.** The relative and range-relative access patterns, with
the retention floor they require. An ordinal axis.

**Models and agents.** A real provider behind the tool-aware interface. A
tool-use loop. Write grants and the enforced carve-out.

**The table.** The responsible adult. A presenter beyond text. Sync
estimation between clocks.

**Trust and the market.** Signed manifests. Attestation. Escrow receipts.
A description whose reference cannot be run by the buyer. An audition
against real weights.

**Grounding.** The real-weights canary in stenota (`HARDWARE-TODO.md`,
item H11). The hardware measurements that replace the illustrative costs.

## What is next

The order below is a proposal, chosen so that each step makes the demos
more real and removes a "not" from the table above. The founder decides.

1. ~~**Persistence.**~~ Done 2026-09-16, with loading, editing and
   inspection (the inspector), activation, routing, and tracking, in the
   order the founder set.
2. ~~**Decisions and presentations on the table.**~~ Done 2026-09-18, with
   deferral: a step that needs a model this process lacks becomes a
   request node another agent fulfils.
3. **A real model behind the tool-aware interface, and a tool-use loop.**
   Turns the chat demo into something that answers, and gives an agent a
   way to act, which is the first step toward the butler. **Handed to the
   Claude Code session on the DGX Spark, 2026-09-18** (`handoff/SPARK.md`):
   the first exchange store holds two requests waiting for a real model,
   and the two sessions pass work through git.
4. **Concurrent and change-driven production.** Makes "async" true rather
   than nominal.
5. **A transport for replicas.** Makes "distributable" true for state;
   executing on a named executor follows it.
6. **Kinds with schemas, then the store kinds.** Closes the universality gap.
7. **Grants and the responsible adult.** Closes the agentic gap on the safe
   side.

The real-weights canary runs on a laptop in parallel with all of this.

## How to check any of this

```bash
cd backend && python3 -m pytest tests/temporal/ -q       # the suite
cd backend && PYTHONPATH=. python3 ../demos/chat_live_graph.py
cd backend && PYTHONPATH=. python3 ../demos/table_attention.py
cd backend && PYTHONPATH=. python3 ../demos/table_chat.py
cd backend && PYTHONPATH=. python3 ../demos/inspector/server.py --demo   # then open http://127.0.0.1:8765/
cd backend && PYTHONPATH=. python3 ../handoff/fulfil.py --store ../handoff/stores/exchange-1 --provider echo --by me --dry-run   # what waits for a model
cd backend && PYTHONPATH=. python3 ../handoff/seed_exchange.py --verify   # has the Spark answered yet
python3 handoff/note.py list                              # what the sessions owe each other
cd spikes/placement-bench && python3 bench.py             # measured placement
```

The vault's `LLM_Wiki/state-of-play.md` is the live page across all the
repositories; this file is the code-side status and is updated with each
slice.
