# placement-bench — the plan as an artifact, on stenota's real graph

Spike, not core. Measures `nodecules/core/placement.py` (PR-d2) on the
real `stenota.v0` graph — 11 nodes, 15 edges, copied from stenota at
`0c50e5d` — with three illustrative executors: a MacBook Air
(`on-device`), a DGX-class LAN runner (`lan`), and the cloud. Runs in a
fifth of a second:

```bash
cd spikes/placement-bench
python3 bench.py
```

**The costs are illustrative.** Seconds per meeting-hour, guessed at the
right order of magnitude, deliberately heterogeneous (the cloud is the
better ASR and LLM, the LAN box the better diarizer). `HARDWARE-TODO` H10
replaces them with measured numbers. The findings below are about
*shapes*; do not quote the digits as facts about hardware.

## Experiments

| | question | finding |
|---|---|---|
| P1 | does an unsatisfiable lock fail at plan time? | `full-airgap` with an Air that has no local LLM: the plan is refused whole, naming the three LLM jobs and, per executor, why — "no claim for `llm-lens/v1`" on the device, "lock does not admit `lan`/`cloud`" for the rest |
| P2 | region vs per-node binding on a real graph | at 1× crossing cost region saves 3%; per-node holds 4 crossings at every scale because it cannot see them; region drops to 2 as crossings get dearer; per-node penalty is **52% at 3×, 162% at 10×** — E7's shape, on the real graph |
| P3 | do lock levels move compute? | `open` and `no-model-egress` both land on the LAN box (the cloud wins ASR and LLM individually, not as a region); `full-airgap` collapses onto the device at 8265 vs 490 — the price is paid in compute, visibly, not in a violation |
| P4 | is warm residency a placement input? | a 25-unit cold start does not move ASR off the LAN box at these costs; it is on the table for the measured ones |
| P5 | where did my data go? | under `open`, nine strips touched the cloud; under `no-model-egress`, **none** — derived from the plan record, not from trusting the runtime |
| P6 | is the plan re-verifiable? | honest plan passes; a forged policy hash and a halved cost summary are both caught from content |

## What the bench taught

**Data locality is a placement constraint, not a cost.** The first run
collapsed every scenario onto one executor, because nothing said the
media file lives on the device. `Job.pinned_to` fixed that, and the pin's
reason appears on every other executor's exclusion — the plan says why
the crossing was unavoidable.

**Per-node binding is blind, not merely greedy.** Its crossing count is
constant across the sweep because crossings are not in its objective at
all; it is not making a bad trade-off, it is making none. This is the
sharper statement of E7.

**A partial plan is worse than no plan.** P1 refuses the whole graph when
three jobs are unplaceable rather than placing the other eight. Dropping
work silently is exactly the failure a plan exists to prevent.

## Honest scope

- Illustrative costs (above). Boundary costs are a per-locality-pair
  scalar; real transfer cost depends on what is crossing (a sidecar of
  JSONL versus an hour of PCM).
- Three executors, eleven nodes: branch-and-bound is instant. It is
  exhaustive in the worst case and capped at sixteen nodes; larger graphs
  need a partitioner, and the cap is a refusal, not a fallback.
- No dispatch. The plan is the decision; nothing here runs a node
  anywhere, moves bytes between executors (the store's sparse-replica
  tier, PR-r3), or enforces per-strip grant scopes (vault P-13).
- Assays are trivially passing fixtures. The interplay of a *failing*
  assay with placement is covered by the core tests, not measured here.
