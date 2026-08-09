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
