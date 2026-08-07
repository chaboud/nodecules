# identity-bench

A throwaway spike, not the new core. It exists to put numbers against four
design assertions that had been argued but never measured.

```bash
cd spikes/identity-bench && python3 bench.py
```

Pure stdlib, no deps, ~600 lines. Run it before trusting any of the claims
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

## What this does *not* test

Honest scope. None of the expensive parts are here:

- no distribution, no network, no two machines
- no description matching or negotiation (see E3)
- no sync, no compaction, no eviction actually running
- no attestation
- single-process, single-writer, one language

## Files

- `cas.py` — content-addressed node identity, node/graph hashing, merkle roll-up
- `nodes.py` — colour conversion, perturbing nodes, IIR/FIR
- `classify.py` — AST-based perturbation + coverage classifier
- `bench.py` — the four experiments
