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
    Executor,
    Job,
    Placement,
    PlacementGraph,
    Policy,
    candidates,
    data_flow,
    lock_admits,
    plan,
    verify_plan,
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
