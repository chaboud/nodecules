"""The matching bench. Run: python3 bench.py

Six experiments against P-27's three unmeasured things — the matching, the
decision, and the equivalence argument. Every one prints a measurement or a
PASS/FAIL rather than an opinion.
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


if __name__ == "__main__":
    m1_structural()
    m2_assay()
    receipt = m3_decision()
    m4_tolerance_forbids()
    m5_composition()
    m6_verify(receipt)
    print()
