# matching-bench — the satisfies judgment, measured

Spike, not core. Pure stdlib; shares the toy colour chain with
`../identity-bench` (both retire together when absorbed). Runs in about a
second:

```bash
cd spikes/matching-bench
python3 bench.py
```

## What it tests

P-27 said the identity bench proved a rewrite is *visible* but that nothing
had ever **received a description and decided** — the matching, the decision,
and the equivalence argument were the unmeasured half of ADR-0010, and the
last untested pillar of the one-node-model (ADR-0018's named falsifier is the
satisfies judgment). This bench does exactly what P-27 prescribes: a worker is
handed a description ("`color.rgb` in, `color.yuv` out, tolerance t") plus its
own inventory of realizations, and has to produce a graph.

The description shape (`descriptions.py`) is the smallest one that makes
"does this concrete plan do what that description says?" checkable:
`consumes` / `produces` kinds, a `tolerance`, and a `reference` realization
the description ships with — ADR-0014's ingot in its conformance-oracle role.
The description is itself content-addressed (interface + tolerance +
reference hash), so editing any of them makes a *different* description,
per the interface-immutability rule.

## The finding that matters

**The satisfies judgment is two judgments, and both are load-bearing.**

- The **valence check** (structural): do the plan's ends and internal bonds
  line up with the description's kinds? Decidable from interfaces alone —
  and *provably insufficient alone*: the bench includes an impostor
  (`rgb_to_yuv_bt709`) whose interface is byte-identical to the honest
  casting's. The structural judgment cannot distinguish them, ever.
- The **assay** (empirical): run the candidate against the reference on
  sampled inputs, measure deviation. This is what tells the casting
  (worst 5.6e-16, ~2.5 ULP) from the impostor (worst 1.2e-1) — fourteen
  orders of magnitude apart, invisible to the type system.

This mirrors the two-layer identity split (ADR-0003): the structural
judgment lives with intent, the empirical one with realization. Collapsing
them into one "does it match?" would rebuild the single-graded-axis mistake
ADR-0003 already unpicked.

## Experiments

| | question | finding |
|---|---|---|
| M1 | does interface search find the candidates? | 3 plans from a 5-realization inventory, including the impostor — **structurally indistinguishable from the casting** |
| M2 | can the assay tell them apart? | casting 5.6e-16 worst, impostor 1.2e-1 worst; the impostor is caught **only by running it** |
| M3 | does the worker find the fused path? | yes, chosen as cheapest passing plan (4.2× cheaper than the chain); receipt says `via-substitute` with a different hash — honest by construction |
| M4 | what happens when tolerance forbids it? | at 1e-17 the fused path fails the assay; worker falls back to the reference, receipt says `exact` |
| M5 | can it produce a *graph*, not pick a node? | with no direct realization it composes the two-step chain by interface chaining; with no path at all it returns **no binding** — the query fails ordinarily (E_NOINTERFACE) |
| M6 | is the receipt independently checkable? | verifier re-runs on its own samples: honest receipt passes; forged outcome caught by hash-derivability; swapped plan caught by hash mismatch |

**The unplanned finding:** the impostor is also the *cheapest*
structurally-valid plan (6.2ms vs the casting's 9.9ms per 20k samples — one
matmul, no clamping). A matcher that chose cheapest-structurally-valid
without the assay would not occasionally err; it would **preferentially
select the wrong answer**, because doing less work is correlated with
skipping it. Cost pressure actively drives toward impostors. The assay is
not a safety net; it is the thing the decision is made *of*.

**On the receipt (ADR-0019):** outcome is *derivable* from the hashes
(`exact` iff plan hash = reference hash), which is what made the forged
outcome in M6 mechanically catchable. A receipt field that cannot be
recomputed from content is a field a verifier must take on faith — worth
keeping as a design rule when the hallmark grows a signature.

## Honest scope

- Synthetic, single process, deterministic nodes only. The `equivalent`
  regime (nondeterministic recipes, where checking degrades from empirical
  to reputational — ADR-0019's own caveat) is **not exercised** here.
- `tolerance` is a scalar. The real criterion is margin-relative
  (E5, P-28): the margin distribution is per-workload and drifts. A scalar
  is the right first cut and the wrong last one.
- The structural search is exhaustive over a 5-realization inventory. It
  says nothing about search at registry scale, ontology matching, or
  decoration beyond cost — P-11's WIT question is untouched.
- Cost is measured wall time in-process; no cold-start, residency, or
  locality axes (executors.md's decoration list).
- The reference realization is trusted by fiat. Nothing here attests *the
  reference itself* — that is the ingot/casting trust chain (ADR-0014,
  P-29), not this bench.
- No signature. The receipt is the hallmark's payload, not a hallmark.

P-references (P-11, P-27, P-28) are the vault's open-questions list
(ChaboudPrivateWiki `LLM_Wiki/primitive/open-questions.md`); ADRs are its
`LLM_Wiki/decisions/`.
