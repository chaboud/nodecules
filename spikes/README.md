# spikes/

Throwaway benches. **Not the new core, not a dependency of anything, not
imported by `backend/`.** They exist to put numbers against design assertions
that had been argued but never measured, so that ADRs cite measurements instead
of intuitions.

Delete a spike freely once its findings are absorbed into the design and the
code that replaces it. Leaving a spike around after it has been superseded is
the same failure as `plan-of-record.md` — a document that reads as current and
isn't.

## `identity-bench/`

Pure stdlib Python, no dependencies, plus one optional Node check. Runs in about
a second.

```bash
cd spikes/identity-bench
python3 bench.py      # seven experiments
node enforce.mjs      # the WASM sandbox check from E6
```

Seven experiments and what they actually found — full detail in its own
`README.md`:

| | question | finding |
|---|---|---|
| E1 | hash the code instead of declaring a version? | **hash the AST, not the source** — a comment edit must not invalidate a cache |
| E2 | can perturbation be detected automatically? | 8/8 from a ~120-line AST walker, **but** it falsified the single-axis model: determinism and coverage are two properties |
| E3 | is a graph rewrite visible, and does it change the answer? | visible by construction; only 19.5% bit-identical, at ~1 ULP — so tolerance-declaring is the **common** case |
| E4 | does checkpointing bound an IIR node's retention? | checkpointed resume is **exact**; the window without one is `n = log(tol)/log(1−α)` |
| E5 | when does a small difference stop being small? | sharp onset where perturbation meets the **1st-percentile decision margin**; rounding-scale is 14 orders below it |
| E6 | is a WASM import section a *proof* of coverage? | yes — the import-free module runs with `{}`, the one importing `env.now` **refuses to instantiate** |
| E7 | is per-node binding good enough? | no — penalty grows unbounded with boundary cost, and at low cost it silently spent **6,668×** more tolerance budget |

Two of these corrected a stated position rather than confirming it (E2 and E5),
which is the point of building them.

**Honest scope:** single process, single writer, no network, no real ingot
compiled from a real language, and E5/E7's absolute numbers belong to synthetic
models. The *shapes* are robust; don't quote the percentages.

## `matching-bench/`

Pure stdlib; shares the toy colour chain with `identity-bench` (they retire
together). Runs in about a second:

```bash
cd spikes/matching-bench
python3 bench.py      # six experiments, M1-M6
```

P-27's prescribed bench: a worker is handed only a description
(`color.rgb` in, `color.yuv` out, tolerance t) plus its own realization
inventory, and has to produce a graph — the matching, the decision, and the
equivalence argument that the identity bench left unmeasured. Six
experiments and what they found — full detail in its own `README.md`:

| | question | finding |
|---|---|---|
| M1 | does interface search find the candidates? | yes, including an **impostor** (BT.709 constants) structurally indistinguishable from the honest casting |
| M2 | can the assay tell them apart? | casting 5.6e-16 worst deviation, impostor 1.2e-1 — caught **only by running it** |
| M3 | does the worker find the fused path? | yes; cheapest passing plan, receipt says `via-substitute` with a different hash |
| M4 | what if tolerance forbids it? | falls back to the reference realization; receipt says `exact` |
| M5 | produce a *graph*, not pick a node? | composes the two-step chain by interface chaining; with no path, **no binding** — the query fails ordinarily |
| M6 | is the receipt independently checkable? | forged outcome and swapped plan both mechanically caught |

The unplanned finding: the impostor was also the *cheapest* structurally-valid
plan, so a matcher without the assay would preferentially select the wrong
answer — cost pressure actively drives toward impostors. The satisfies
judgment is **two judgments** (a structural valence check and an empirical
assay), mirroring ADR-0003's two identity layers.
