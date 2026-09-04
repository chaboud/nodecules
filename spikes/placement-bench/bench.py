"""The placement bench. Run: python3 bench.py

Six experiments on stenota's real 11-node graph with three illustrative
executors (a MacBook Air, a LAN DGX-class runner, the cloud). Every one
prints a measurement or a PASS/FAIL rather than an opinion. Costs are
illustrative; the shapes are the findings.
"""

from __future__ import annotations

import time

import fixtures as f
from nodecules.core.placement import data_flow, plan, verify_plan


def hdr(n: int, title: str, cite: str) -> None:
    print(f"\n{'=' * 74}\nP{n}. {title}\n    ({cite})\n{'=' * 74}")


def show(p, label: str) -> None:
    by_ex: dict = {}
    for a in p.assignments:
        by_ex.setdefault(a.executor_id, []).append(a.node_id)
    print(f"  {label:14s} total {p.total_cost:8.1f}  (compute {p.compute_cost:7.1f} + boundary {p.boundary_cost:6.1f}, "
          f"{p.crossings} crossings, {len(p.regions)} regions)")
    for ex, nodes in sorted(by_ex.items()):
        print(f"      {ex:6s} {', '.join(nodes)}")


G = f.graph()


def p1_unsatisfiable() -> None:
    hdr(1, "An unsatisfiable lock fails at plan time, naming the job and the reasons",
        "trust.md 'unsatisfiable policies fail loudly, early'")
    execs = f.executors(mba_has_llm=False)
    r = plan(G, execs, f.SPECS, f.ASSAYS, f.policy("full-airgap"))
    print(f"  full-airgap, MacBook Air without a local LLM: plan = {r.plan}")
    for node, why in r.unsatisfiable.items():
        print(f"  {node}:")
        for ex, reason in why.items():
            print(f"      {ex:6s} {reason}")
    print("\n  => the failure is a planning error with the job named, not a runtime")
    print("     surprise. Everything else in the graph was placeable; the plan")
    print("     is refused whole, because a partial plan silently drops work.")


def p2_region_vs_per_node() -> None:
    hdr(2, "Region binding vs per-node binding on the real graph",
        "ADR-0015; identity-bench E7 measured this on a synthetic graph")
    execs = f.executors()
    pol = f.policy("open")
    t0 = time.perf_counter(); pn = plan(G, execs, f.SPECS, f.ASSAYS, pol, method="per-node").plan; t1 = time.perf_counter()
    rg = plan(G, execs, f.SPECS, f.ASSAYS, pol, method="region").plan; t2 = time.perf_counter()
    show(pn, "per-node")
    show(rg, "region")
    saved = pn.total_cost - rg.total_cost
    print(f"\n  region saves {saved:.1f} ({100 * saved / pn.total_cost:.1f}%) by paying "
          f"{rg.compute_cost - pn.compute_cost:+.1f} compute to avoid {pn.boundary_cost - rg.boundary_cost:.1f} of crossings")
    print(f"  per-node planned in {1000 * (t1 - t0):.1f} ms; region (branch-and-bound, 11 nodes x 3 executors) in {1000 * (t2 - t1):.1f} ms")

    print("\n  the same graph as crossing cost scales (E7's claim on a real graph):")
    print(f"  {'scale':>6s} {'per-node':>10s} {'xings':>6s} {'region':>10s} {'xings':>6s} {'penalty':>9s}")
    for scale in (0.0, 0.1, 0.3, 1.0, 3.0, 10.0):
        pol_s = f.Policy(lock_level="open", boundary_cost={k: v * scale for k, v in f.BOUNDARY.items()},
                         default_boundary=scale)
        a = plan(G, execs, f.SPECS, f.ASSAYS, pol_s, method="per-node").plan
        b = plan(G, execs, f.SPECS, f.ASSAYS, pol_s, method="region").plan
        print(f"  {scale:6.1f} {a.total_cost:10.1f} {a.crossings:6d} {b.total_cost:10.1f} {b.crossings:6d} "
              f"{100 * (a.total_cost - b.total_cost) / b.total_cost:8.1f}%")
    print("  => per-node's crossing count never changes (it cannot see crossings);")
    print("     region's falls as crossings get dearer, and the per-node penalty grows")
    print("     without bound with boundary cost - E7's shape, on the real graph.")


def p3_locks_move_compute() -> None:
    hdr(3, "Lock levels are planning constraints: the same graph, three placements",
        "keyhole ADR-0004 lock levels; trust.md 'binding respects locks at plan time'")
    execs = f.executors()
    for lock in ("open", "no-model-egress", "full-airgap"):
        p = plan(G, execs, f.SPECS, f.ASSAYS, f.policy(lock)).plan
        show(p, lock)
        asr = next(a for a in p.assignments if a.node_id == "asr")
        print(f"      asr -> {asr.executor_id} running {asr.realization}")
    print("\n  => no-model-egress removes the cloud and the plan re-forms around the")
    print("     LAN runner; full-airgap collapses everything onto the device and the")
    print("     price is paid in compute, visibly, rather than in a policy violation.")


def p4_warm_residency() -> None:
    hdr(4, "Warm residency is a placement input", "executors.md decoration axis 'residency'")
    pol = f.policy("no-model-egress")
    warm = plan(G, f.executors(spark_warm=True), f.SPECS, f.ASSAYS, pol).plan
    cold = plan(G, f.executors(spark_warm=False), f.SPECS, f.ASSAYS, pol).plan
    show(warm, "spark warm")
    show(cold, "spark cold")
    print(f"\n  cold start adds {cold.total_cost - warm.total_cost:.1f}; with these costs it "
          f"{'does' if warm.executor_of('asr') != cold.executor_of('asr') else 'does not'} move asr")


def p5_where_did_my_data_go() -> None:
    hdr(5, "Where did my data go — answered from the plan record alone",
        "trust.md 'privacy claims must be falsifiable'")
    execs = f.executors()
    for lock in ("open", "no-model-egress"):
        p = plan(G, execs, f.SPECS, f.ASSAYS, f.policy(lock)).plan
        flow = data_flow(p, G)
        leaked = sorted(s for s, exs in flow.items() if "cloud" in exs)
        print(f"  {lock:16s} strips that touched the cloud: {leaked or 'none'}")
    print("\n  => under no-model-egress the answer is 'none', and it is derived from")
    print("     the plan, not from trusting the runtime to have behaved.")


def p6_the_artifact() -> None:
    hdr(6, "The plan is content-addressed and re-verifiable", "ADR-0019 receipts; ADR-0022 decoration")
    execs = f.executors()
    pol = f.policy("no-model-egress")
    p = plan(G, execs, f.SPECS, f.ASSAYS, pol).plan
    print(f"  plan hash    : {p.content_hash()[:24]}")
    print(f"  graph hash   : {p.graph_hash[:24]}   policy hash: {p.policy_hash[:24]}")
    ok, why = verify_plan(p, G, execs, pol)
    print(f"  honest plan  : {'PASS' if ok else 'FAIL'} — {why}")
    forged = p.model_copy(update={"policy_hash": f.policy('open').content_hash()})
    ok2, why2 = verify_plan(forged, G, execs, pol)
    print(f"  forged policy: {'PASS' if ok2 else 'CAUGHT'} — {why2}")
    cheaper = p.model_copy(update={"compute_cost": p.compute_cost / 2})
    ok3, why3 = verify_plan(cheaper, G, execs, pol)
    print(f"  forged cost  : {'PASS' if ok3 else 'CAUGHT'} — {why3}")
    a = next(a for a in p.assignments if a.node_id == "summarizer_l2")
    print(f"\n  a reason, verbatim: {a.reason}")


if __name__ == "__main__":
    p1_unsatisfiable()
    p2_region_vs_per_node()
    p3_locks_move_compute()
    p4_warm_residency()
    p5_where_did_my_data_go()
    p6_the_artifact()
    print()
