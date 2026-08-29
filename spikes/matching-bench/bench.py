"""The matching bench. Run: python3 bench.py

Eight experiments. M1-M6 measure P-27's three unmeasured things — the
matching, the decision, and the equivalence argument. M7-M8 measure P-32's
adversarial half: whether the assay can be gamed, and what workload-drawn
probing buys back. Every one prints a measurement or a PASS/FAIL rather
than an opinion.
"""

from __future__ import annotations

import random

import descriptions as d
import match


def hdr(n: int, title: str, cite: str) -> None:
    print(f"\n{'=' * 74}\nM{n}. {title}\n    ({cite})\n{'=' * 74}")


def sample_rgb(n: int, seed: int) -> list:
    rng = random.Random(seed)
    return [(rng.random(), rng.random(), rng.random()) for _ in range(n)]


SAMPLES = sample_rgb(20000, 20260829)


# --- M1: the valence check finds candidates, and cannot tell them apart -----

def m1_structural() -> None:
    hdr(1, "Valence check: interface search over the inventory",
        "P-27 'the matching'; ADR-0004 capability by description")

    desc = d.describe(tolerance=1e-12)
    plans = match.find_plans(desc, d.standard_inventory())
    print(f"  description: {desc.consumes} -> {desc.produces}"
          f"  (id {d.description_hash(desc)[:12]})")
    print(f"  inventory: {len(d.standard_inventory())} realizations")
    print(f"  structurally valid plans found: {len(plans)}")
    for p in plans:
        names = " -> ".join(r.name for r in p)
        print(f"    {d.plan_hash(p)[:16]}  {names}")

    fused_iface = (d.R_FUSED.consumes, d.R_FUSED.produces)
    impostor_iface = (d.R_IMPOSTOR.consumes, d.R_IMPOSTOR.produces)
    print(f"\n  casting interface : {fused_iface}")
    print(f"  impostor interface: {impostor_iface}")
    print(f"  interfaces identical: {fused_iface == impostor_iface}"
          "  <- the structural judgment cannot tell them apart")


# --- M2: the assay can ------------------------------------------------------

def m2_assay() -> None:
    hdr(2, "Assay: measured deviation against the shipped reference",
        "ADR-0014 ingot as conformance oracle; P-12 resolution")

    desc = d.describe(tolerance=1e-12)
    binding = match.decide(desc, d.standard_inventory(), SAMPLES)
    print(f"  n = {len(SAMPLES)} sampled inputs   tolerance = {desc.tolerance:.1e}\n")
    print(f"  {'plan':42s} {'worst dev':>11s} {'mean dev':>11s} {'cost':>9s}  verdict")
    for a in sorted(binding.assays, key=lambda a: a.worst):
        verdict = "PASS" if a.worst <= desc.tolerance else "REJECT"
        print(f"  {a.names:42s} {a.worst:11.3e} {a.mean:11.3e} {a.cost_s*1000:7.1f}ms  {verdict}")

    print("\n  => same valence, three different answers. The impostor is caught")
    print("     only by running it; no amount of interface inspection finds it.")


# --- M3: the decision, and what the receipt says ----------------------------

def m3_decision() -> match.Receipt:
    hdr(3, "Decision: cheapest plan that passes; the receipt binds it",
        "P-11 dumbest matcher; ADR-0010 returned hash; ADR-0019 hallmark")

    desc = d.describe(tolerance=1e-12)
    binding = match.decide(desc, d.standard_inventory(), SAMPLES)
    r = binding.receipt
    assert r is not None
    print(f"  chosen: {' -> '.join(r.plan_names)}")
    print(f"  receipt:")
    print(f"    graph ran   : {r.graph_hash[:24]}")
    print(f"    reference   : {r.reference_hash[:24]}")
    print(f"    outcome     : {r.outcome}")
    print(f"    worst dev   : {r.worst_deviation:.3e}  (tolerance {r.tolerance:.1e}, n={r.n_samples})")
    print("\n  => the worker found the fused path on its own, and the receipt")
    print("     says so honestly: a different hash, labeled via-substitute.")
    return r


# --- M4: tolerance forbids the substitution ---------------------------------

def m4_tolerance_forbids() -> None:
    hdr(4, "Tolerance forbids: the worker falls back to the reference",
        "P-27 'what it does when the tolerance forbids it'")

    desc = d.describe(tolerance=1e-17)  # below the fused path's ~1 ULP deviation
    binding = match.decide(desc, d.standard_inventory(), SAMPLES)
    r = binding.receipt
    assert r is not None
    print(f"  tolerance = {desc.tolerance:.1e}  (fused path deviates ~1 ULP, so it must fail)")
    print(f"  chosen: {' -> '.join(r.plan_names)}")
    print(f"  outcome: {r.outcome}   worst dev: {r.worst_deviation:.3e}")
    print("\n  => the cheaper plan was on the table and correctly refused; the")
    print("     receipt degrades to the reference realization and says 'exact'.")


# --- M5: composition, and failing ordinarily --------------------------------

def m5_composition() -> None:
    hdr(5, "Composition: produce a graph, not pick a node — and E_NOINTERFACE",
        "P-27 'has to produce a graph'; executors.md query-must-fail")

    desc = d.describe(tolerance=1e-12)

    # No direct rgb->yuv realization in inventory: the worker must chain.
    inv = (d.R_RGB_TO_HSL, d.R_HSL_TO_YUV, d.R_LUMA)
    binding = match.decide(desc, inv, SAMPLES)
    r = binding.receipt
    assert r is not None
    print(f"  inventory without any direct {desc.consumes}->{desc.produces}:")
    print(f"    chosen: {' -> '.join(r.plan_names)}   outcome: {r.outcome}")
    print("    <- a two-step graph assembled by interface chaining, not read")
    print("       from the reference (the search only sees the inventory)")

    # And an inventory that cannot reach the goal at all.
    inv2 = (d.R_RGB_TO_HSL, d.R_LUMA)
    binding2 = match.decide(desc, inv2, SAMPLES)
    print(f"\n  inventory that cannot reach {desc.produces}:")
    print(f"    plans found: {len(binding2.assays)}   binding: {binding2.chosen}")
    print("    <- the query fails ordinarily. No binding is an answer, not an error.")


# --- M6: the hallmark check -------------------------------------------------

def m6_verify(honest_receipt: match.Receipt) -> None:
    hdr(6, "Hallmark check: an independent verifier re-runs and compares",
        "ADR-0019 commitment 2 — receipts are independently checkable")

    desc = d.describe(tolerance=1e-12)
    fresh = sample_rgb(20000, 999)  # the verifier samples for itself

    ok, why = match.verify(honest_receipt, (d.R_FUSED,), desc, fresh)
    print(f"  honest receipt, real plan      : {'PASS' if ok else 'FAIL'} — {why}")

    # Forged outcome: claim the substitution was exact.
    forged = match.Receipt(
        plan_names=honest_receipt.plan_names,
        graph_hash=honest_receipt.graph_hash,
        reference_hash=honest_receipt.reference_hash,
        outcome=match.OUTCOME_EXACT,
        worst_deviation=honest_receipt.worst_deviation,
        tolerance=honest_receipt.tolerance,
        n_samples=honest_receipt.n_samples,
    )
    ok2, why2 = match.verify(forged, (d.R_FUSED,), desc, fresh)
    print(f"  forged outcome ('exact')       : {'PASS' if ok2 else 'CAUGHT'} — {why2}")

    # Swapped plan: hand the verifier the impostor with the casting's receipt.
    ok3, why3 = match.verify(honest_receipt, (d.R_IMPOSTOR,), desc, fresh)
    print(f"  swapped plan (impostor)        : {'PASS' if ok3 else 'CAUGHT'} — {why3}")

    print("\n  => the receipt's honesty is mechanical: hashes bind the plan, the")
    print("     outcome is derivable, and the deviation claim is re-measurable.")


# --- M7: the defeat device — every mechanical check passes ------------------

def m7_defeat_device() -> None:
    hdr(7, "Defeat device: honest on the public suite, wrong in production",
        "P-32 Goodhart; ADR-0021 'certificate over a declared subset'")

    import gamed

    desc = d.describe(tolerance=1e-12)
    public_suite = SAMPLES  # the description's published conformance inputs
    device = gamed.make_defeat_device(frozenset(public_suite))
    r_device = d.Realization(device, "color.rgb", "color.yuv")

    ref_out = [match.run_plan(desc.reference, s) for s in public_suite]
    a = match.assay((r_device,), ref_out, public_suite)
    print(f"  assay on the PUBLIC suite (n={len(public_suite)}):"
          f"  worst dev {a.worst:.3e}  -> {'PASS' if a.worst <= desc.tolerance else 'REJECT'}")

    receipt = match.Receipt(
        plan_names=(r_device.name,), graph_hash=a.graph_hash,
        reference_hash=d.plan_hash(desc.reference),
        outcome=match.OUTCOME_SUBSTITUTE, worst_deviation=a.worst,
        tolerance=desc.tolerance, n_samples=len(public_suite),
    )
    ok, why = match.verify(receipt, (r_device,), desc, public_suite)
    print(f"  hallmark check, SAME suite     : {'PASS' if ok else 'FAIL'} — {why}")

    production = sample_rgb(20000, 424242)  # inputs the device never saw
    prod = match.assay((r_device,), [match.run_plan(desc.reference, s) for s in production], production)
    print(f"  production (fresh inputs)      : worst dev {prod.worst:.3e}")

    print("\n  => every mechanical check passed and production is wrong by"
          f" {prod.worst:.0e}.")
    print("     The receipt is honest; the CLAIM it certifies is too weak. The")
    print("     deception lives entirely in the gap between suite and workload,")
    print("     so probe provenance (which inputs, drawn from where) must be a")
    print("     first-class receipt field — 'passed the public suite' is")
    print("     Goodhart-void as evidence.")


# --- M8: workload-sampled probing — detection scales with harm --------------

def m8_detection_scaling() -> None:
    hdr(8, "Workload-sampled probes: detection probability equals harm rate",
        "P-32 resolution criterion — 'at what sampling rate'")

    import gamed
    import random as _r

    desc = d.describe(tolerance=1e-12)
    public_suite = frozenset(SAMPLES)
    fractions = (0.1, 0.01, 0.001)
    ns = (1, 3, 10, 30, 100, 300, 1000)
    trials = 100

    print(f"  sparse defector: honest on the public suite, defects on a fraction f")
    print(f"  of the input space. {trials} trials per cell; detection = any fresh")
    print(f"  probe deviating past tolerance.\n")
    print("  " + " ".join(f"{'f=' + str(f):>9s}" for f in fractions) + "    n (fresh probes)")

    rng = _r.Random(77)
    need95: dict[float, int | None] = {f: None for f in fractions}
    for n in ns:
        row = []
        for f in fractions:
            device = gamed.make_sparse_defector(public_suite, f)
            detected = 0
            for _ in range(trials):
                probes = [(rng.random(), rng.random(), rng.random()) for _ in range(n)]
                for p in probes:
                    got = device(p)
                    want = d.nodes.rgb_to_yuv(p)
                    if max(abs(x - y) for x, y in zip(got, want)) > desc.tolerance:
                        detected += 1
                        break
            rate = detected / trials
            if rate >= 0.95 and need95[f] is None:
                need95[f] = n
            row.append(f"{rate:9.2f}")
        print("  " + " ".join(row) + f"    {n}")

    print(f"\n  expected: P(detect) = 1-(1-f)^n; 95% needs n ≈ 3/f "
          f"(measured 95% at n = {[need95[f] for f in fractions]} for f = {list(fractions)})")
    print("\n  => when probes are drawn from the workload distribution, per-probe")
    print("     detection probability IS the per-input harm rate: a gamer can only")
    print("     hide defections where the workload does not go — which is where")
    print("     they do not matter. The budget to exclude defection rate f is")
    print("     ~3/f fresh samples, so rare-defection assurance is bought over")
    print("     time by production spot-checks (P-29), not at binding.")


if __name__ == "__main__":
    m1_structural()
    m2_assay()
    receipt = m3_decision()
    m4_tolerance_forbids()
    m5_composition()
    m6_verify(receipt)
    m7_defeat_device()
    m8_detection_scaling()
    print()
