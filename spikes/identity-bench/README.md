# identity-bench

A throwaway spike, not the new core. It exists to put numbers against four
design assertions that had been argued but never measured.

```bash
cd spikes/identity-bench && python3 bench.py
```

Pure stdlib, no deps, ~900 lines. Run it before trusting any of the claims
below.

## What it tests, and what it found

### E1 — node identity from `hash(code)`, not a version string

Both strategies are stable across runs. The difference that matters:

| edit | `source` hash | `ast` hash |
|---|---|---|
| comment only | **changes** | unchanged |
| behaviour change | changes | **changes** |

**Use the AST hash.** A source hash throws away every cached result downstream
of a comment edit. The AST hash (with `include_attributes=False`, so moving a
function in a file is free) invalidates on behaviour and not on formatting.

This closes the gap noted against nodecules `feat/temporality`, where the cache
key carries `node_version` as a *declared string* — a promise rather than a
fact.

### E2 — automatic perturbation classification

**8/8 correct with a ~120-line AST walker.** Clocks, unseeded RNG, and
environment reads are all detected; `random.Random(seed)` is correctly
recognised as the declared-input escape hatch while bare `random.random()` is
not.

**But the bench also falsified something.** `rgb_to_yuv` classifies as `pure`
and is *not* pure with respect to its code hash: it reads module-level
constants `_Y`, `_U`, `_V` that the AST hash does not cover. Edit one of those
and the function's behaviour changes while its identity does not — precisely
the silent, unrecoverable error the whole scheme exists to prevent.

So **determinism and coverage are two properties, not one**:

- *deterministic* — output is a function of its inputs (what the classifier checks)
- *covered* — every one of those inputs is inside the hash (what it can only report on)

A node needs both to be safely reusable. The classifier finds the coverage gaps
automatically, which is the encouraging half: an automatic manifest is
achievable, so it will actually get produced.

### E3 — worker rewrites `RGB→HSL→YUV` into a fused `RGB→YUV`

Composed graph hashes differ, so **the rewrite is visible by construction** —
a caller cannot be silently handed a different computation.

Over 20,000 random RGB triples:

| | |
|---|---|
| bit-identical results | **3,903 / 20,000 (19.5%)** |
| mean absolute difference | 9.7 × 10⁻¹⁷ |
| worst absolute difference | 6.1 × 10⁻¹⁶ (**2.8 ULPs**) |

Semantically supplanting, numerically not identical — as designed. But the
distribution is the finding: **80.5% of results differ, and they differ by
about one ULP.** Tiny in magnitude, near-universal in occurrence.

Consequence: `declared-equivalent within a tolerance` is not an edge case, it
is the **common case**. A system that treats every rewrite as identity-breaking
will essentially never reuse a cached result across a rewrite, and the whole
rewriting story stops paying for itself. The tolerance-declaring path has to be
cheap and ergonomic from day one.

**Not tested here:** negotiation. The bench *constructs* both graphs and
compares them; it does not have a worker *receive a description and decide* to
rewrite. That is the harder half of the claim and it is still unmeasured.

### E4 — does checkpointing bound an IIR node's retention window?

α = 0.05, 4,000 samples, cut at n = 2,000.

| resume strategy | error |
|---|---|
| **with** checkpointed state hashed in | **0.0 — exact** |
| without it (window the input only) | 0.458 at the first sample |

And the window needed if you *don't* checkpoint is closed-form,
`n = log(tol)/log(1−α)`:

| tolerance | samples to retain |
|---|---|
| 10⁻³ | 135 |
| 10⁻⁶ | 270 |
| 10⁻⁹ | 405 |

Linear in the number of digits — every 1000× tighter tolerance costs another
135 samples at this α. Contrast FIR(32): exactly 32 samples, at any tolerance,
forever.

**Checkpoint cadence is the floor of the retention envelope for a stateful
node**, and the cost of not checkpointing is computable rather than unbounded.
That is a much better position than "IIR breaks eviction."

### E5 — discrete-stateful substitution: when does a small difference stop being small?

E3 is *stateless*: two equivalent graphs differ per-sample and it never
accumulates. Face re-identification is the same **kind** of substitution — two
matchers, same declared intent, semantic rather than precise replacements — but
it is **discrete and stateful**: it assigns an observation to a track and then
updates that track. The hypothesis was that a tiny difference would flip a
near-threshold match and diverge permanently.

**Two null results first, both informative.**

1. A *uniform* perturbation `d * (1 + jitter)` never changes anything at any
   scale — it is a monotone transform, so it preserves the ordering of
   distances exactly and cannot flip an argmin. **An implementation difference
   that perturbs every comparison identically is safe by construction.**
2. Even with a per-comparison *independent* perturbation, ULP-scale jitter still
   changed nothing — because the decisions were nowhere near close enough.

So the question isn't "how big is the difference," it's "how big relative to
the decision margins." Measured, for this workload:

| | p1 | median |
|---|---|---|
| argmin margin (`d₂ − d₁`) | 1.13 × 10⁻³ | 1.09 × 10⁻¹ |
| threshold gap (\|d₁ − τ\|) | 5.86 × 10⁻³ | 1.99 × 10⁻¹ |

And the sweep, 300 observations of 6 people, τ = 0.45, EMA α = 0.3:

| relative jitter | tracks | pairwise agreement | first divergence | re-converged | frames diverged after |
|---|---|---|---|---|---|
| 10⁻¹⁵ … 10⁻² | 30 | **100.000%** | never | — | — |
| 3 × 10⁻² | 29 | 98.685% | frame 114 | yes | 81/186 (**44%**) |
| 10⁻¹ | 30 | 95.434% | frame 22 | yes | 229/278 (**82%**) |

**A sharp onset between 10⁻² and 3 × 10⁻² relative** — exactly where the
absolute perturbation reaches the 1st-percentile decision margin. Below it,
nothing at all. Above it, divergence that *partly* re-converges (the EMA update
pulls the trackers back together) but leaves 44–82% of subsequent frames
disagreeing.

Three consequences:

- **The three regimes are real but the middle one was mis-stated.** Stateless
  substitution perturbs *bounded*; stable-stateful perturbs *decaying* with a
  computable horizon (E4); discrete-stateful has a **threshold** — nothing, then
  a sharp onset, then persistent-but-intermittent divergence. Not "permanent,"
  which was the guess.
- **Rounding-scale substitution is safe even here.** E3's ~10⁻¹⁶ is *fourteen
  orders of magnitude* below the onset. The danger is not a fused kernel or a
  different SIMD width; it is a genuinely different model, embedding, or
  threshold — differences of 10⁻² and up.
- **Substitutability is measurable, per deployment.** Take the margin
  distribution of your actual workload; the low-percentile margin is the
  tolerance you can accept. That is a computable criterion, not a judgement
  call — and it is exactly why empirical quality has to be per-deployment: the
  margin distribution belongs to *your* cameras, *your* population, *your*
  workload.

## What this does *not* test

Honest scope. None of the expensive parts are here:

- no distribution, no network, no two machines
- no description matching or negotiation (see E3, E5)
- E5 uses a synthetic embedding space, not a real re-id model — the *shape* is
  right, the absolute margin numbers belong to this toy
- no sync, no compaction, no eviction actually running
- no attestation
- single-process, single-writer, one language

## Files

- `cas.py` — content-addressed node identity, node/graph hashing, merkle roll-up
- `nodes.py` — colour conversion, perturbing nodes, IIR/FIR
- `classify.py` — AST-based perturbation + coverage classifier
- `reid.py` — nearest-centroid tracker, decision margins, divergence profile
- `bench.py` — the five experiments
