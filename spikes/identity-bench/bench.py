"""The bench. Run: python3 bench.py  (then: node enforce.mjs)

Seven experiments, each pressure-testing one design assertion. Every one prints
a PASS/FAIL/NUMBER rather than an opinion.
"""

from __future__ import annotations

import math

import cas
import classify
import nodes


def hdr(n: int, title: str, adr: str) -> None:
    print(f"\n{'=' * 74}\nE{n}. {title}\n    ({adr})\n{'=' * 74}")


# --- E1: is hash(code) a usable substitute for a version string? -----------

def e1_identity() -> None:
    hdr(1, "Node identity from hash(code), not a declared version string",
        "ADR-0003 lower layer; nodecules feat/temporality gap")

    for strat in ("source", "ast"):
        h1 = cas.code_hash(nodes.rgb_to_yuv, strat)
        h2 = cas.code_hash(nodes.rgb_to_yuv, strat)
        print(f"  {strat:6s}  stable across calls: {h1 == h2}   {h1[:16]}")

    # A comment-only edit, simulated by hashing two source strings that differ
    # only in a comment.
    import ast as _ast
    import hashlib

    a = "def f(x):\n    return x + 1\n"
    b = "def f(x):\n    # a comment that changes nothing\n    return x + 1\n"
    src_differs = hashlib.sha256(a.encode()).hexdigest() != hashlib.sha256(b.encode()).hexdigest()
    ast_differs = (_ast.dump(_ast.parse(a), include_attributes=False)
                   != _ast.dump(_ast.parse(b), include_attributes=False))
    print(f"\n  comment-only edit -> source hash changes: {src_differs}")
    print(f"  comment-only edit -> ast    hash changes: {ast_differs}")
    print("  => `ast` avoids throwing away every cached result on a comment edit.")

    # A real behaviour change must change the hash under both.
    c = "def f(x):\n    return x + 2\n"
    ast_changed = (_ast.dump(_ast.parse(a), include_attributes=False)
                   != _ast.dump(_ast.parse(c), include_attributes=False))
    print(f"  behaviour change  -> ast    hash changes: {ast_changed}  (required)")


# --- E2: can perturbation be detected automatically? -----------------------

def e2_classification() -> None:
    hdr(2, "Automatic perturbation classification",
        "ADR-0009 default-to-perturbing; P-25 coverage manifest")

    expected = {
        "rgb_to_yuv": "pure", "rgb_to_hsl": "pure", "hsl_to_yuv": "pure",
        "scale": "pure", "seeded_jitter": "pure",
        "stamp_now": "perturbing", "jitter": "perturbing",
        "read_env_scale": "perturbing",
    }
    fns = [nodes.rgb_to_yuv, nodes.rgb_to_hsl, nodes.hsl_to_yuv, nodes.scale,
           nodes.seeded_jitter, nodes.stamp_now, nodes.jitter, nodes.read_env_scale]

    right = 0
    for fn in fns:
        v = classify.classify(fn)
        ok = v.identity_kind == expected[fn.__name__]
        right += ok
        print(v.line() + ("" if ok else "   <-- MISCLASSIFIED"))
    print(f"\n  {right}/{len(fns)} classified correctly with a ~120-line AST walker.")
    print("  Note `seeded_jitter`: random.Random(seed) is recognised as the")
    print("  declared-input escape hatch, while bare random.random() is not.")


# --- E3: the rewrite. Does it show up, and does it change the answer? ------

def e3_rewrite() -> None:
    hdr(3, "Worker rewrites RGB->HSL->YUV into a fused RGB->YUV",
        "ADR-0010 binding is negotiation; ADR-0009 rewrite-is-perturbation")

    long_hash, long_nodes = cas.build_chain([nodes.rgb_to_hsl, nodes.hsl_to_yuv])
    fused_hash, fused_nodes = cas.build_chain([nodes.rgb_to_yuv])

    print(f"  submitted graph  rgb->hsl->yuv   composed: {long_hash[:24]}")
    print(f"  worker returned  rgb->yuv        composed: {fused_hash[:24]}")
    print(f"  hashes differ: {long_hash != fused_hash}  <- the rewrite is visible by construction")

    # Now: are the answers the same?
    import random as _r
    _r.seed(20260729)
    samples = [(_r.random(), _r.random(), _r.random()) for _ in range(20000)]

    worst = 0.0
    worst_rgb = None
    total = 0.0
    exact = 0
    for rgb in samples:
        a = nodes.hsl_to_yuv(nodes.rgb_to_hsl(rgb))
        b = nodes.rgb_to_yuv(rgb)
        d = max(abs(x - y) for x, y in zip(a, b))
        total += d
        if d == 0.0:
            exact += 1
        if d > worst:
            worst, worst_rgb = d, rgb

    print(f"\n  n = {len(samples)} random RGB triples")
    print(f"  bit-identical results : {exact}/{len(samples)}  ({100*exact/len(samples):.1f}%)")
    print(f"  mean abs difference   : {total/len(samples):.3e}")
    print(f"  worst abs difference  : {worst:.3e}   at rgb={tuple(round(v,4) for v in worst_rgb)}")
    print(f"  worst, in ULPs of 1.0 : {worst / 2.220446049250313e-16:.1f}")
    print("\n  => semantically supplanting, numerically NOT identical.")
    print("     A rewrite is a perturbation. Empirical confidence measured on the")
    print("     long graph does not transfer to the fused one for free.")


# --- E4: does checkpointing bound an IIR node's retention window? ----------

def e4_iir_retention() -> None:
    hdr(4, "IIR retention window: checkpoint vs. no checkpoint",
        "ADR-0013 retention envelope; ADR-0009 escape hatch")

    import random as _r
    _r.seed(1)
    stream = [_r.random() for _ in range(4000)]
    alpha = 0.05
    cut = 2000

    full, _ = nodes.iir_lowpass(stream, alpha)

    # (a) resume WITH the checkpointed state hashed in as a declared input
    _, state_at_cut = nodes.iir_lowpass(stream[:cut], alpha)
    resumed_ck, _ = nodes.iir_lowpass(stream[cut:], alpha, state=state_at_cut)
    err_ck = max(abs(x - y) for x, y in zip(resumed_ck, full[cut:]))

    # (b) resume WITHOUT it — the naive "just window the input" approach
    resumed_naive, _ = nodes.iir_lowpass(stream[cut:], alpha, state=0.0)
    err_naive_first = abs(resumed_naive[0] - full[cut])
    err_naive_max = max(abs(x - y) for x, y in zip(resumed_naive, full[cut:]))

    print(f"  alpha = {alpha}, stream = {len(stream)} samples, cut at n = {cut}")
    print(f"  resume WITH checkpoint    -> max error {err_ck:.3e}  (exact: {err_ck == 0.0})")
    print(f"  resume WITHOUT checkpoint -> error at first sample {err_naive_first:.3e}")
    print(f"                               max error over tail   {err_naive_max:.3e}")

    for tol in (1e-3, 1e-6, 1e-9):
        n = nodes.iir_error_horizon(alpha, tol)
        print(f"  retention needed for tol {tol:.0e} without a checkpoint: {n:>5d} samples")

    fir_taps = 32
    print(f"\n  contrast: FIR({fir_taps}) needs exactly {fir_taps} samples, at any tolerance.")
    print("\n  => checkpoint cadence IS the floor of the retention envelope for a")
    print("     stateful node, and the required window without one is computable:")
    print("     n = log(tol)/log(1-alpha).")


def e5_reid_divergence() -> None:
    hdr(5, "Discrete-stateful substitution: when does a small difference stop being small?",
        "ADR-0009 identity classes; ADR-0010 declared-equivalent tolerance")

    import reid

    obs, truth = reid.make_stream(n_people=6, n_frames=300, dim=8, sigma=0.9, seed=7)
    TAU, ALPHA = 0.45, 0.3
    base, n_base = reid.track(obs, TAU, ALPHA, jitter=0.0)

    am, tg = reid.decision_margins(obs, TAU, ALPHA)
    am_s, tg_s = sorted(am), sorted(tg)
    pct = lambda v, q: v[int(len(v) * q)]

    print(f"  reference matcher: {n_base} tracks over {len(obs)} obs, {len(set(truth))} true people")
    print(f"  decision margins   argmin: p1={pct(am_s,.01):.2e}  median={pct(am_s,.5):.2e}")
    print(f"                  threshold: p1={pct(tg_s,.01):.2e}  median={pct(tg_s,.5):.2e}")
    print("\n  A perturbation far below these margins cannot change any decision.\n")

    print(f"  {'jitter':>8} {'tracks':>7} {'agree':>10} {'1st div':>8} {'reconv':>7} {'diverged after':>16}")
    print("  " + "-" * 62)
    for jit in (1e-15, 1e-9, 1e-4, 1e-3, 3e-3, 1e-2, 3e-2, 1e-1):
        a, n = reid.track(obs, TAU, ALPHA, jitter=jit)
        ag = reid.pairwise_agreement(base, a)
        first, recon, after = reid.divergence_profile(base, a)
        fd = "never" if first < 0 else str(first)
        tail = "-" if first < 0 else f"{after}/{len(obs)-first} ({after/(len(obs)-first):.0%})"
        print(f"  {jit:>8.0e} {n:>7} {ag:>9.3%} {fd:>8} {str(recon):>7} {tail:>16}")

    print("\n  => Sharp onset between 1e-2 and 3e-2 relative, which is exactly where the")
    print("     absolute perturbation reaches the 1st-percentile decision margin.")
    print("     Below it: nothing. Above it: divergence that partly re-converges but")
    print("     leaves 44-82% of subsequent frames disagreeing.")
    print("\n     E3's rounding difference (~1e-16) is FOURTEEN orders of magnitude below")
    print("     the onset. Rounding-scale substitution is safe even here. Model-scale")
    print("     substitution (a different embedder, a different threshold) is not.")
    print("     Substitutability is not about the size of the difference — it is about")
    print("     that size RELATIVE TO THE DECISION MARGINS, which are measurable.")


def e6_wasm_manifest() -> None:
    hdr(6, "A portable unit of compute: is the WASM import section a *proof* of coverage?",
        "ADR-0003 identity; ADR-0009 determinism-vs-coverage; P-25 manifest")

    import wasm_ingot as w

    pure, pert = w.pure_module(), w.perturbing_module()
    for label, mod in (("pure", pure), ("perturbing", pert)):
        imps = w.read_imports(mod)
        manifest = ", ".join(str(i) for i in imps) if imps else "NONE"
        print(f"  {label:<11} {len(mod):>3} bytes  hash {w.module_hash(mod)[:16]}")
        print(f"  {'':<11} coverage manifest = imports: {manifest}")

    print("\n  Identity: the bytes are already canonical, so E1's source-vs-AST")
    print("  dilemma does not arise at this layer at all — and it is language-agnostic.")
    print("\n  Coverage: E2's AST walker was a best-effort scanner that could miss an")
    print("  unknown-unknown. A WASM module has NO ambient authority — no clock, RNG,")
    print("  env, filesystem or network — unless the host hands it an import. So the")
    print("  import section is a COMPLETE declaration of everything reachable outside")
    print("  its own bytes. Run `node enforce.mjs` to see the sandbox enforce it:")
    print("  the pure module runs with {}, the perturbing one refuses to instantiate.")


def e7_placement() -> None:
    hdr(7, "Binding granularity: is per-ingot choice good enough?",
        "ADR-0010 negotiation; ADR-0014 castings; boundary cost")

    import placement as pl

    g = pl.greedy_per_node()
    o = pl.optimal_latency()
    print(f"  {'plan':<28} {'latency':>9} {'crossings':>10} {'regions':>8} {'deviation':>11}")
    print("  " + "-" * 70)
    print(f"  {'per-ingot greedy  ' + g.shape():<28} {g.latency:>8.1f}ms {g.crossings:>10} {g.regions():>8} {g.deviation:>11.2e}")
    print(f"  {'region-optimal    ' + o.shape():<28} {o.latency:>8.1f}ms {o.crossings:>10} {o.regions():>8} {o.deviation:>11.2e}")

    print("\n  Greedy ignores edges, so its plan never changes. The penalty grows with")
    print("  boundary cost, and the optimum collapses into fewer regions:\n")
    print(f"  {'x crossing':>11} {'greedy':>10} {'optimal':>10} {'penalty':>10} {'opt regions':>12}")
    for f in (0.0, 1.0, 2.0, 4.0, 8.0, 16.0):
        cx = pl.scaled_cross(f)
        gp, op = pl.evaluate(g.assign, cx), pl.optimal_latency(cx)
        pen = 100 * (gp.latency - op.latency) / op.latency
        print(f"  {f:>10.1f}x {gp.latency:>9.1f}ms {op.latency:>9.1f}ms {pen:>9.1f}% {op.regions():>12}")

    front = pl.pareto(pl.all_plans())
    print(f"\n  Pareto front over (latency, deviation): {len(front)} non-dominated plans")
    print(f"  {'latency':>9} {'deviation':>11} {'regions':>8}  shape")
    for p in front:
        print(f"  {p.latency:>8.1f}ms {p.deviation:>11.2e} {p.regions():>8}  {p.shape()}")

    ratio = g.deviation / o.deviation
    print(f"\n  => Two findings. Greedy's latency penalty grows without bound as boundaries")
    print(f"     get expensive (0% -> 561% here). And at 1x it silently spent {ratio:.0f}x more")
    print("     of the TOLERANCE budget, picking DSP purely on speed.")
    print("     Per-ingot ranking optimises one axis and blows another.")
    print("     There is no single best plan — the caller's declared tolerance picks a")
    print("     point on the front, and boundary cost sets how fine-grained")
    print("     specialisation can usefully be.")


def main() -> None:
    print("identity-bench — pressure testing ADR-0003 / 0009 / 0010 / 0013")
    print("throwaway spike; nothing here is the new core")
    e1_identity()
    e2_classification()
    e3_rewrite()
    e4_iir_retention()
    e5_reid_divergence()
    e6_wasm_manifest()
    e7_placement()
    print()


if __name__ == "__main__":
    main()
