"""Tests for PR-d2 — placement, the plan as an artifact."""

from __future__ import annotations

import pytest

from nodecules.core.descriptions import (
    DECORATION_NAMESPACES,
    Description,
    ProducedStrip,
    SatisfiesClaim,
    Tolerance,
    run_assay,
)
from nodecules.core.placement import (
    HEURISTICS,
    BoundaryPrice,
    CostVector,
    Edge,
    boundary_kind,
    Executor,
    Job,
    Placement,
    PlacementGraph,
    Policy,
    candidates,
    data_flow,
    lock_admits,
    plan,
    plan_front,
    reconcile,
    verify_plan,
    Observation,
)
from nodecules.core.types import NodeSpec


# --- fixtures ------------------------------------------------------------------

def _desc(name: str, strip: str) -> Description:
    return Description(name=name, consumes=(), produces=(ProducedStrip(strip_name=strip, schema_id="S"),),
                       tolerance=Tolerance(metric="max_abs", max_value=0.0), reference=f"ref.{name}")


def _spec(realization: str, strip: str, deterministic: bool = True) -> NodeSpec:
    return NodeSpec(node_type=realization, display_name=realization, description="",
                    writes_strips=[strip], is_deterministic=deterministic)


def _world(names):
    """Descriptions, specs, and passing assays for realizations `ref.<name>`."""
    descs, specs, assays = {}, {}, {}
    for n in names:
        d = _desc(n, f"strips/{n}")
        r = f"ref.{n}"
        descs[n] = d
        specs[r] = _spec(r, f"strips/{n}")
        assays[r] = run_assay(d, r, [], [], n_probes=1, probe_provenance="fresh-drawn", declared_deterministic=True)
    return descs, specs, assays


def _claims(descs, names):
    return tuple(SatisfiesClaim(realization=f"ref.{n}", description_hash=descs[n].content_hash(), claimant="t")
                 for n in names)


def _chain():
    """A -> B -> C. X is cheap for A and C, dear for B; Y is cheap for B."""
    descs, specs, assays = _world(["a", "b", "c"])
    graph = PlacementGraph(graph_id="chain", jobs=(
        Job(node_id="A", description=descs["a"], reads=("in",), writes=("strips/a",)),
        Job(node_id="B", description=descs["b"], reads=("strips/a",), writes=("strips/b",)),
        Job(node_id="C", description=descs["c"], reads=("strips/b",), writes=("strips/c",)),
    ), edges=(("A", "B"), ("B", "C")))
    x = Executor(executor_id="X", locality="on-device", claims=_claims(descs, ["a", "b", "c"]),
                 cost={"ref.a": 1.0, "ref.b": 10.0, "ref.c": 1.0})
    y = Executor(executor_id="Y", locality="lan", claims=_claims(descs, ["a", "b", "c"]),
                 cost={"ref.a": 5.0, "ref.b": 1.0, "ref.c": 5.0})
    return graph, (x, y), specs, assays, descs


class TestLocks:
    def test_admission_table(self) -> None:
        assert lock_admits("open", "cloud")
        assert not lock_admits("no-model-egress", "cloud")
        assert lock_admits("no-model-egress", "lan")
        assert not lock_admits("full-airgap", "lan")
        assert lock_admits("full-airgap", "on-device")

    def test_excluded_executor_carries_the_reason(self) -> None:
        graph, (x, y), specs, assays, _ = _chain()
        cloud = Executor(executor_id="Z", locality="cloud", claims=x.claims, cost=x.cost)
        admitted, excluded = candidates(graph.jobs[0], (x, y, cloud), specs, assays,
                                        Policy(lock_level="no-model-egress"))
        assert set(admitted) == {"X", "Y"}
        assert "does not admit 'cloud'" in excluded["Z"]

    def test_executor_without_claim_is_excluded(self) -> None:
        graph, (x, y), specs, assays, descs = _chain()
        mute = Executor(executor_id="M", locality="on-device", claims=_claims(descs, ["b"]), cost={"ref.b": 1.0})
        admitted, excluded = candidates(graph.jobs[0], (mute,), specs, assays, Policy())
        assert admitted == {} and "no claim for 'a'" in excluded["M"]

    def test_pinned_job_excludes_everything_else_with_reason(self) -> None:
        graph, (x, y), specs, assays, _ = _chain()
        pinned = graph.model_copy(update={"jobs": (graph.jobs[0].model_copy(update={"pinned_to": "X"}),) + graph.jobs[1:]})
        admitted, excluded = candidates(pinned.jobs[0], (x, y), specs, assays, Policy())
        assert set(admitted) == {"X"} and "pinned to 'X'" in excluded["Y"]
        # A pin to an executor the lock forbids is unsatisfiable, loudly.
        r = plan(pinned, (x, y), specs, assays, Policy(lock_level="full-airgap"))
        assert r.plan is not None  # X is on-device, so this one is fine
        y_pinned = graph.model_copy(update={"jobs": (graph.jobs[0].model_copy(update={"pinned_to": "Y"}),) + graph.jobs[1:]})
        r2 = plan(y_pinned, (x, y), specs, assays, Policy(lock_level="full-airgap"))
        assert r2.plan is None and "A" in r2.unsatisfiable
        assert pinned.content_hash() != graph.content_hash()

    def test_unsatisfiable_fails_at_plan_time_with_names(self) -> None:
        graph, (x, y), specs, assays, _ = _chain()
        only_cloud = Executor(executor_id="Z", locality="cloud", claims=x.claims, cost=x.cost)
        p = plan(graph, (only_cloud,), specs, assays, Policy(lock_level="full-airgap"))
        assert p.plan is None
        assert set(p.unsatisfiable) == {"A", "B", "C"}
        assert "full-airgap" in p.unsatisfiable["A"]["Z"]


class TestRegionVsPerNode:
    def test_per_node_pays_the_crossings_it_ignored(self) -> None:
        graph, execs, specs, assays, _ = _chain()
        policy = Policy(default_boundary=20.0)
        pn = plan(graph, execs, specs, assays, policy, method="per-node").plan
        assert pn is not None
        assert [a.executor_id for a in pn.assignments] == ["X", "Y", "X"]
        assert pn.crossings == 2 and pn.compute_cost == 3.0 and pn.boundary_cost == 40.0

    def test_region_absorbs_a_dear_node_to_avoid_crossings(self) -> None:
        graph, execs, specs, assays, _ = _chain()
        policy = Policy(default_boundary=20.0)
        rg = plan(graph, execs, specs, assays, policy, method="region").plan
        assert rg is not None
        # Consolidating on either executor beats crossing; Y (5+1+5) edges
        # X (1+10+1). The optimizer found the cheaper of the two.
        assert {a.executor_id for a in rg.assignments} == {"Y"}
        assert rg.crossings == 0 and rg.total_cost == 11.0
        assert rg.regions == (("Y", ("A", "B", "C")),)

    def test_region_splits_when_crossings_are_cheap(self) -> None:
        graph, execs, specs, assays, _ = _chain()
        rg = plan(graph, execs, specs, assays, Policy(default_boundary=0.1), method="region").plan
        assert rg is not None
        assert [a.executor_id for a in rg.assignments] == ["X", "Y", "X"]
        assert rg.total_cost == pytest.approx(3.2)
        assert len(rg.regions) == 3

    def test_region_is_never_worse_than_per_node(self) -> None:
        graph, execs, specs, assays, _ = _chain()
        for b in (0.0, 0.5, 3.0, 9.0, 50.0):
            policy = Policy(default_boundary=b)
            pn = plan(graph, execs, specs, assays, policy, method="per-node").plan
            rg = plan(graph, execs, specs, assays, policy, method="region").plan
            assert rg.total_cost <= pn.total_cost + 1e-9

    def test_warm_residency_flips_a_choice(self) -> None:
        graph, (x, y), specs, assays, _ = _chain()
        cold_y = y.model_copy(update={"cold_start": {"ref.b": 20.0}})
        warm_y = cold_y.model_copy(update={"warm": ("ref.b",)})
        policy = Policy(default_boundary=0.1)
        cold = plan(graph, (x, cold_y), specs, assays, policy).plan
        warm = plan(graph, (x, warm_y), specs, assays, policy).plan
        assert cold.executor_of("B") == "X" and warm.executor_of("B") == "Y"

    def test_lock_changes_the_plan(self) -> None:
        graph, execs, specs, assays, _ = _chain()
        open_plan = plan(graph, execs, specs, assays, Policy(default_boundary=0.1)).plan
        airgap = plan(graph, execs, specs, assays, Policy(lock_level="full-airgap", default_boundary=0.1)).plan
        assert open_plan.executor_of("B") == "Y"
        assert {a.executor_id for a in airgap.assignments} == {"X"}
        assert "does not admit 'lan'" in airgap.excluded["B"]["Y"]


class TestTheArtifact:
    def test_data_flow_is_complete(self) -> None:
        graph, execs, specs, assays, _ = _chain()
        p = plan(graph, execs, specs, assays, Policy(default_boundary=0.1)).plan
        flow = data_flow(p, graph)
        assert flow == {"in": ("X",), "strips/a": ("X", "Y"), "strips/b": ("X", "Y"), "strips/c": ("X",)}

    def test_reasons_name_alternatives_and_exclusions(self) -> None:
        graph, execs, specs, assays, _ = _chain()
        p = plan(graph, execs, specs, assays, Policy(lock_level="full-airgap")).plan
        a = p.assignments[1]
        assert a.node_id == "B" and "admissible: X" in a.reason and "excluded: Y" in a.reason

    def test_plan_hash_is_stable_and_verifies(self) -> None:
        graph, execs, specs, assays, _ = _chain()
        policy = Policy(default_boundary=0.1)
        p1 = plan(graph, execs, specs, assays, policy).plan
        p2 = plan(graph, execs, specs, assays, policy).plan
        assert p1.content_hash() == p2.content_hash()
        ok, why = verify_plan(p1, graph, execs, policy)
        assert ok, why

    def test_verify_catches_tampered_cost_and_wrong_policy(self) -> None:
        graph, execs, specs, assays, _ = _chain()
        policy = Policy(default_boundary=0.1)
        p = plan(graph, execs, specs, assays, policy).plan
        cheaper = p.model_copy(update={"compute_cost": p.compute_cost - 1.0})
        assert verify_plan(cheaper, graph, execs, policy)[0] is False
        assert verify_plan(p, graph, execs, Policy(lock_level="full-airgap", default_boundary=0.1))[0] is False

    def test_verify_catches_assignment_the_lock_forbids(self) -> None:
        graph, execs, specs, assays, _ = _chain()
        p = plan(graph, execs, specs, assays, Policy(default_boundary=0.1)).plan
        forged_policy = Policy(lock_level="full-airgap", default_boundary=0.1)
        forged = p.model_copy(update={"policy_hash": forged_policy.content_hash()})
        ok, why = verify_plan(forged, graph, execs, forged_policy)
        assert not ok and "not admitted" in why

    def test_graph_and_policy_hashes_track_content(self) -> None:
        graph, *_ = _chain()
        assert graph.content_hash() == graph.model_copy().content_hash()
        assert Policy().content_hash() != Policy(lock_level="full-airgap").content_hash()

    def test_plans_and_executors_are_decoration_namespaces(self) -> None:
        assert "plans/" in DECORATION_NAMESPACES and "executors/" in DECORATION_NAMESPACES

    def test_cycle_is_rejected(self) -> None:
        graph, execs, specs, assays, _ = _chain()
        cyclic = graph.model_copy(update={"edges": graph.edges + (("C", "A"),)})
        with pytest.raises(ValueError, match="cycle"):
            plan(cyclic, execs, specs, assays, Policy())


# --- The cost model: vectors, biases, boundary kinds, compound heuristics ------


def _same_host_pair():
    """A -> B -> C on one host: a CPU executor and a pipelined GPU executor.
    The GPU is far cheaper for B alone; the bus between them is cheap."""
    descs, specs, assays = _world(["a", "b", "c"])
    graph = PlacementGraph(graph_id="pp", jobs=(
        Job(node_id="A", description=descs["a"]),
        Job(node_id="B", description=descs["b"]),
        Job(node_id="C", description=descs["c"]),
    ), edges=(Edge(source="A", target="B", bytes=1e6), Edge(source="B", target="C", bytes=1e6)))
    cpu = Executor(executor_id="cpu", locality="on-device", host="air", device="cpu",
                   claims=_claims(descs, ["a", "b", "c"]), cost={"ref.a": 1.0, "ref.b": 10.0, "ref.c": 1.0})
    gpu = Executor(executor_id="gpu", locality="on-device", host="air", device="gpu", pipelined=True,
                   claims=_claims(descs, ["a", "b", "c"]), cost={"ref.a": 4.0, "ref.b": 1.0, "ref.c": 4.0})
    return graph, (cpu, gpu), specs, assays, descs


class TestCostModel:
    def test_boundary_kind_is_derived_from_where_executors_are(self) -> None:
        graph, (cpu, gpu), *_ = _same_host_pair()
        lan_box = Executor(executor_id="box", locality="lan", host="box")
        cloud = Executor(executor_id="cloud", locality="cloud")
        assert boundary_kind(cpu, gpu) == "bus"
        assert boundary_kind(cpu, lan_box) == "lan"
        assert boundary_kind(lan_box, cloud) == "wan"

    def test_bytes_cross_at_the_boundary_price(self) -> None:
        graph, execs, specs, assays, _ = _same_host_pair()
        policy = Policy(boundary={"bus": BoundaryPrice(fixed=CostVector(latency=0.5),
                                                       per_byte=CostVector(latency=1e-6, energy=2e-6))})
        cpu, gpu = execs
        kind, cost = policy.crossing(cpu, gpu, 1e6)
        assert kind == "bus"
        assert cost == CostVector(latency=1.5, energy=2.0)

    def test_scalar_costs_still_mean_latency(self) -> None:
        graph, (x, y), specs, assays, _ = _chain()
        assert x.cost_of("ref.b") == CostVector(latency=10.0)

    def test_weights_are_the_biases_that_flip_a_plan(self) -> None:
        # Y is faster but power-hungry; X is slow and frugal. A phone weighs
        # energy, a datacenter weighs latency — same graph, two plans.
        descs, specs, assays = _world(["a"])
        graph = PlacementGraph(graph_id="one", jobs=(Job(node_id="A", description=descs["a"]),))
        x = Executor(executor_id="X", locality="on-device", claims=_claims(descs, ["a"]),
                     cost={"ref.a": CostVector(latency=10.0, energy=1.0)})
        y = Executor(executor_id="Y", locality="lan", claims=_claims(descs, ["a"]),
                     cost={"ref.a": CostVector(latency=2.0, energy=20.0)})
        datacenter = plan(graph, (x, y), specs, assays, Policy(weights={"latency": 1.0})).plan
        phone = plan(graph, (x, y), specs, assays, Policy(weights={"latency": 1.0, "energy": 5.0})).plan
        assert datacenter.executor_of("A") == "Y" and phone.executor_of("A") == "X"
        assert phone.compute == CostVector(latency=10.0, energy=1.0)
        assert phone.objective == pytest.approx(15.0)

    def test_memory_capacity_is_a_constraint_with_a_reason(self) -> None:
        descs, specs, assays = _world(["a"])
        big = Job(node_id="A", description=descs["a"], memory_bytes=12e9)
        small = Executor(executor_id="S", locality="on-device", claims=_claims(descs, ["a"]),
                         cost={"ref.a": 1.0}, memory_bytes=8e9)
        admitted, excluded = candidates(big, (small,), specs, assays, Policy())
        assert admitted == {} and "8e+09" in excluded["S"] and "1.2e+10" in excluded["S"]

    def test_without_heuristics_the_plan_pingpongs(self) -> None:
        graph, execs, specs, assays, _ = _same_host_pair()
        policy = Policy(boundary={"bus": BoundaryPrice(fixed=CostVector(latency=1.0))})
        p = plan(graph, execs, specs, assays, policy).plan
        assert [a.executor_id for a in p.assignments] == ["cpu", "gpu", "cpu"]
        assert p.crossings == 2 and p.objective == pytest.approx(1 + 1 + 1 + 1 + 1)
        assert p.heuristic_hits == ()

    def test_pingpong_heuristic_keeps_the_chain_together_and_says_so(self) -> None:
        graph, execs, specs, assays, _ = _same_host_pair()
        policy = Policy(boundary={"bus": BoundaryPrice(fixed=CostVector(latency=1.0))},
                        heuristics={"pingpong": 8.0})
        p = plan(graph, execs, specs, assays, policy).plan
        # Not "everything on one executor": the heuristic forbids *bouncing*,
        # not crossing. One crossing into the GPU and staying there (1+1+4+1)
        # beats all-GPU (4+1+4) and beats the round trip.
        assert [a.executor_id for a in p.assignments] == ["cpu", "gpu", "gpu"]
        assert p.crossings == 1 and p.objective == pytest.approx(7.0)
        assert p.heuristic_hits == ()
        # And the bouncing plan, verified under the same policy, shows the hit.
        bounced = plan(graph, execs, specs, assays,
                       policy.model_copy(update={"heuristics": {}})).plan
        ev_hit = plan(graph, execs, specs, assays, policy, method="per-node").plan
        assert [a.executor_id for a in ev_hit.assignments] == ["cpu", "gpu", "cpu"]
        assert ev_hit.heuristic_hits[0].name == "pingpong"
        assert ev_hit.heuristic_hits[0].nodes == ("A", "B", "C")
        assert ev_hit.heuristic_cost == pytest.approx(1 * (8.0 - 1))  # the return crossing, x(factor-1)
        assert bounced.objective < ev_hit.objective  # same assignment, the heuristic is the difference

    def test_pingpong_fires_at_any_distance(self) -> None:
        # A -> B -> C -> D with A and D on cpu, B and C on gpu: the return
        # crossing C -> D completes a round trip that started two hops up.
        descs, specs, assays = _world(["a", "b", "c", "d"])
        graph = PlacementGraph(graph_id="long", jobs=tuple(Job(node_id=n.upper(), description=descs[n]) for n in "abcd"),
                               edges=(("A", "B"), ("B", "C"), ("C", "D")))
        cpu = Executor(executor_id="cpu", locality="on-device", host="h", claims=_claims(descs, list("abcd")),
                       cost={"ref.a": 1.0, "ref.b": 10.0, "ref.c": 10.0, "ref.d": 1.0})
        gpu = Executor(executor_id="gpu", locality="on-device", host="h", device="gpu", claims=_claims(descs, list("abcd")),
                       cost={"ref.a": 5.0, "ref.b": 1.0, "ref.c": 1.0, "ref.d": 5.0})
        policy = Policy(boundary={"bus": BoundaryPrice(fixed=CostVector(latency=1.0))}, heuristics={"pingpong": 3.0})
        naive = plan(graph, (cpu, gpu), specs, assays, policy, method="per-node").plan
        assert [a.executor_id for a in naive.assignments] == ["cpu", "gpu", "gpu", "cpu"]
        assert [h.nodes for h in naive.heuristic_hits] == [("A", "C", "D")]
        assert naive.heuristic_cost == pytest.approx(2.0)

    def test_pipeline_fill_flush_charges_each_crossing_touching_the_gpu(self) -> None:
        graph, execs, specs, assays, _ = _same_host_pair()
        policy = Policy(boundary={"bus": BoundaryPrice(fixed=CostVector(latency=1.0))},
                        heuristics={"pipeline_fill_flush": 3.0})
        p = plan(graph, execs, specs, assays, policy, method="per-node").plan
        names = [h.name for h in p.heuristic_hits]
        assert names == ["pipeline_fill_flush", "pipeline_fill_flush"]
        assert p.heuristic_cost == pytest.approx(6.0)

    def test_unknown_heuristic_is_refused(self) -> None:
        graph, execs, specs, assays, _ = _chain()
        with pytest.raises(ValueError, match="unknown heuristic"):
            plan(graph, execs, specs, assays, Policy(heuristics={"vibes": 1.0}))
        assert set(HEURISTICS) == {"pingpong", "pipeline_fill_flush"}

    def test_verify_recomputes_heuristics_and_objective(self) -> None:
        graph, execs, specs, assays, _ = _same_host_pair()
        policy = Policy(boundary={"bus": BoundaryPrice(fixed=CostVector(latency=1.0))},
                        heuristics={"pingpong": 8.0})
        p = plan(graph, execs, specs, assays, policy, method="per-node").plan
        ok, why = verify_plan(p, graph, execs, policy)
        assert ok, why
        quiet = p.model_copy(update={"heuristic_cost": 0.0, "objective": p.objective - p.heuristic_cost})
        assert verify_plan(quiet, graph, execs, policy)[0] is False
        no_hits = p.model_copy(update={"heuristic": CostVector()})
        assert verify_plan(no_hits, graph, execs, policy)[0] is False


# --- The front, and observations: identity and observability ------------------


class TestFrontAndObservations:
    def _tradeoff(self):
        descs, specs, assays = _world(["a", "b"])
        graph = PlacementGraph(graph_id="tr", jobs=(Job(node_id="A", description=descs["a"]),
                                                    Job(node_id="B", description=descs["b"])),
                               edges=(("A", "B"),))
        fast = Executor(executor_id="fast", locality="lan", claims=_claims(descs, ["a", "b"]),
                        cost={"ref.a": CostVector(latency=1, energy=10), "ref.b": CostVector(latency=1, energy=10)})
        frugal = Executor(executor_id="frugal", locality="on-device", claims=_claims(descs, ["a", "b"]),
                          cost={"ref.a": CostVector(latency=5, energy=1), "ref.b": CostVector(latency=5, energy=1)})
        return graph, (fast, frugal), specs, assays

    def test_front_is_the_non_dominated_set(self) -> None:
        graph, execs, specs, assays = self._tradeoff()
        policy = Policy(boundary={"lan": BoundaryPrice(fixed=CostVector(latency=3, energy=3))})
        _, front = plan_front(graph, execs, specs, assays, policy)
        vectors = [(p.compute + p.boundary + p.heuristic) for p in front]
        # all-fast (2, 20), all-frugal (10, 2), and the two mixed plans at
        # (9, 14) — a crossing-paying plan can still be non-dominated: it is
        # faster than frugal and leaner than fast. Nothing beats it on both.
        assert {(v.latency, v.energy) for v in vectors} == {(2.0, 20.0), (9.0, 14.0), (10.0, 2.0)}
        assert len(front) == 4  # the two mixed plans are distinct assignments
        for p in front:
            assert p.method == "front"

    def test_scalar_plan_is_a_point_on_the_front(self) -> None:
        graph, execs, specs, assays = self._tradeoff()
        policy = Policy(boundary={"lan": BoundaryPrice(fixed=CostVector(latency=3, energy=3))},
                        weights={"latency": 1.0, "energy": 1.0})
        chosen = plan(graph, execs, specs, assays, policy).plan
        _, front = plan_front(graph, execs, specs, assays, policy)
        assert any(p.assignments == chosen.assignments for p in front)
        # A different bias picks a different point; the front did not change.
        _, front2 = plan_front(graph, execs, specs, assays, policy.model_copy(update={"weights": {"energy": 1.0}}))
        key = lambda p: tuple(a.executor_id for a in p.assignments)
        assert {key(p) for p in front} == {key(p) for p in front2}
        assert key(front2[0]) == ("frugal", "frugal")  # the energy bias sorts the lean point first

    def test_front_respects_unsatisfiable(self) -> None:
        graph, execs, specs, assays = self._tradeoff()
        placement, front = plan_front(graph, execs, specs, assays, Policy(lock_level="full-airgap"))
        assert placement.plan is not None and front and all(p.executor_of("A") == "frugal" for p in front)
        only_lan = (execs[0],)
        placement2, front2 = plan_front(graph, only_lan, specs, assays, Policy(lock_level="full-airgap"))
        assert placement2.plan is None and front2 == () and "A" in placement2.unsatisfiable

    def test_observations_reconcile_against_the_plan_by_identity(self) -> None:
        graph, execs, specs, assays, _ = _chain()
        policy = Policy(default_boundary=0.1)
        p = plan(graph, execs, specs, assays, policy).plan  # X, Y, X
        obs = [
            Observation(plan_hash=p.content_hash(), subject="B", executor_id="Y", realization="ref.b",
                        measured=CostVector(latency=3.0), source="hardware-run:test"),
            Observation(plan_hash=p.content_hash(), subject="edge:A->B", executor_id="Y",
                        measured=CostVector(latency=0.4), source="hardware-run:test"),
            Observation(plan_hash="someone-elses-plan", subject="B", executor_id="Y",
                        measured=CostVector(latency=99.0), source="noise"),
            Observation(plan_hash=p.content_hash(), subject="B", executor_id="X",
                        measured=CostVector(latency=99.0), source="not-where-it-ran"),
        ]
        disc = reconcile(p, obs, graph, execs, policy)
        assert [(d.subject, d.executor_id) for d in disc] == [("B", "Y"), ("edge:A->B", "Y")]
        assert disc[0].declared.latency == 1.0 and disc[0].ratio_latency == 3.0
        assert disc[1].declared.latency == pytest.approx(0.1) and disc[1].ratio_latency == pytest.approx(4.0)

    def test_observations_are_decoration(self) -> None:
        assert "observations/" in DECORATION_NAMESPACES
        o = Observation(plan_hash="p", subject="A", executor_id="X", measured=CostVector(latency=1.0), source="s")
        assert o.content_hash() == o.model_copy().content_hash()
