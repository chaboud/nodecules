"""Tests for PR-r5a — generation over the store, on a stenota-shaped graph.

Realizations are mocks (weights are the laptop's job); the graph shape is
stenota's: audio → asr, diar → turns → windowed claims."""

from __future__ import annotations

import pytest

from nodecules.core.generation import (
    PARAMS_ROLE,
    RECIPE_ROLE,
    RECIPE_TEMPLATE_KIND,
    Generator,
    NoRealization,
    Realization,
    select,
)
from nodecules.core.placement import Assignment, CostVector, Plan
from nodecules.core.store import Edge, Node, Store, envelope_id
from nodecules.core.strip_access import LatestPattern, RangePattern, SelfWindowEnd, SelfWindowStart
from nodecules.core.time import TimeRange

M = "project/stenota/meeting/abc123"
LIB = "project/stenota/library/recipes"


# --- realizations -----------------------------------------------------------------


async def cook_asr(inputs, params):
    path = inputs["audio"]["path"]
    return [{"text": f"{path}-seg{i}", "time_range": {"start_ms": i * 1000, "end_ms": (i + 1) * 1000}} for i in range(3)]


class Counter:
    n = 0


async def cook_diar(inputs, params):
    Counter.n += 1
    return [{"speaker": f"S{Counter.n}", "time_range": {"start_ms": 0, "end_ms": 3000}}]


async def cook_turns(inputs, params):
    speaker = inputs["diar"][0]["speaker"]
    return [{"speaker": speaker, **seg} for seg in inputs["asr"]]


async def cook_claims(inputs, params):
    return {"claims": [t["text"] for t in inputs["turns"]], "window": params["window"]}


async def cook_asr_fast(inputs, params):
    out = await cook_asr(inputs, params)
    return [{**s, "text": s["text"].upper()} for s in out]


class LyingCounter:
    n = 0


async def cook_liar(inputs, params):
    LyingCounter.n += 1
    return {"n": LyingCounter.n}


ASR = Realization("mock.asr@1", cook_asr, deterministic=True)
ASR_FAST = Realization("mock.asr_fast@1", cook_asr_fast, deterministic=True)
DIAR = Realization("mock.diar@1", cook_diar)  # undeclared: perturbing
TURNS = Realization("mock.turns@1", cook_turns, deterministic=True)
CLAIMS = Realization("mock.claims@1", cook_claims, deterministic=True)
LIAR = Realization("mock.liar@1", cook_liar, deterministic=True)  # claims determinism, is not
INVENTORY = [ASR, ASR_FAST, DIAR, TURNS, CLAIMS, LIAR]


def template(name: str, realization: str, params=None) -> Node:
    return Node(id=f"recipes/{name}", kind=RECIPE_TEMPLATE_KIND, scope=LIB, data={"realization": realization, "params": params or {}})


def recipe(name: str) -> Edge:
    return Edge(target=f"recipes/{name}", scope=LIB, role=RECIPE_ROLE)


def build_graph(store: Store, audio_path: str = "meeting.wav") -> None:
    Counter.n = 0
    tx = store.transaction(LIB, author="librarian")
    for t in (template("asr", ASR.handle), template("diar", DIAR.handle), template("turns", TURNS.handle), template("claims", CLAIMS.handle)):
        tx.put(t)
    tx.commit("recipes")
    tx = store.transaction(M, author="stenota")
    tx.put(Node(id="audio.wav", kind="raw.audio", scope=M, data={"path": audio_path}))
    tx.put(Node(id="strips/asr/segments", kind="asr.segment", scope=M, edges=(Edge(target="audio.wav", pattern=LatestPattern(), role="audio"), recipe("asr"))))
    tx.put(Node(id="strips/diar/segments", kind="diar.segment", scope=M, edges=(Edge(target="audio.wav", pattern=LatestPattern(), role="audio"), recipe("diar"))))
    tx.put(Node(id="strips/turns", kind="turn", scope=M, edges=(Edge(target="strips/asr/segments", role="asr"), Edge(target="strips/diar/segments", role="diar"), recipe("turns"))))
    tx.put(Node(id="params/w1", kind="params", scope=M, data={"window": {"start_ms": 0, "end_ms": 2000}}))
    tx.put(
        Node(
            id="claims/L2#w1",
            kind="claim.L2",
            scope=M,
            edges=(
                Edge(target="strips/turns", pattern=RangePattern(field="time_range", start=SelfWindowStart(), end=SelfWindowEnd()), role="turns"),
                recipe("claims"),
                Edge(target="params/w1", role=PARAMS_ROLE),
            ),
        )
    )
    tx.commit("declare the graph")


# --- the four outcomes, and the triggers ------------------------------------------


@pytest.mark.asyncio
async def test_cold_start_produces_the_chain_in_dependency_order():
    store = Store()
    build_graph(store)
    g = Generator(store, INVENTORY)
    out = await g.produce(M, "claims/L2#w1")
    assert out.outcome == "exact" and out.cooked and not out.cache_hit
    assert out.node.data["claims"] == ["meeting.wav-seg0", "meeting.wav-seg1"]  # the Range pattern kept two of three
    assert g.cooks == 4  # asr, diar, turns, claims
    m = store.current(M)
    for name in ("strips/asr/segments", "strips/diar/segments", "strips/turns", "claims/L2#w1"):
        assert isinstance(store.get(m, name), Node) and store.get(m, name).data is not None
        env = store.get(m, envelope_id(name))
        assert isinstance(env, Node) and env.data["cache_key"] and env.data["recipe"]["realization"]
    assert m.author == "generator"
    # reproducibility follows declared determinism on a first production, and says it is unmeasured
    asr_env = store.get(m, envelope_id("strips/asr/segments")).data
    diar_env = store.get(m, envelope_id("strips/diar/segments")).data
    assert (asr_env["reproducibility"], asr_env["measured"]) == ("exact", False)
    assert (diar_env["reproducibility"], diar_env["measured"]) == ("equivalent", False)


@pytest.mark.asyncio
async def test_second_production_is_a_cache_hit_everywhere():
    store = Store()
    build_graph(store)
    g = Generator(store, INVENTORY)
    await g.produce(M, "claims/L2#w1")
    seq = store.current(M).seq
    out = await g.produce(M, "claims/L2#w1")
    assert out.cache_hit and not out.cooked and g.cooks == 4
    assert store.current(M).seq == seq  # nothing committed


@pytest.mark.asyncio
async def test_stale_recook_follows_the_changed_input_and_only_it():
    store = Store()
    build_graph(store)
    g = Generator(store, INVENTORY)
    await g.produce(M, "claims/L2#w1")
    # a sibling nobody reads: cached, untouched
    tx = store.transaction(M)
    tx.put(Node(id="audio.wav", kind="raw.audio", scope=M, data={"path": "retake.wav"}))
    tx.commit("new audio")
    out = await g.produce(M, "claims/L2#w1")
    assert out.cooked and out.node.data["claims"][0] == "retake.wav-seg0"
    assert g.cooks == 8  # every node downstream of audio recooked
    # changing only the window params recooks claims, not its upstream
    tx = store.transaction(M)
    tx.put(Node(id="params/w1", kind="params", scope=M, data={"window": {"start_ms": 0, "end_ms": 3000}}))
    tx.commit("wider window")
    out = await g.produce(M, "claims/L2#w1")
    assert out.cooked and len(out.node.data["claims"]) == 3 and g.cooks == 9


@pytest.mark.asyncio
async def test_resurrection_measures_reproducibility_and_cascades_from_a_nondeterministic_upstream():
    store = Store()
    build_graph(store)
    g = Generator(store, INVENTORY)
    first = await g.produce(M, "claims/L2#w1")
    m = store.current(M)
    asr_hash = store.get(m, "strips/asr/segments").content_hash()
    diar_hash = store.get(m, "strips/diar/segments").content_hash()
    # prune a deterministic node's data: rebuilt identically, reproducibility now *measured* exact
    store.prune(asr_hash)
    out = await g.produce(M, "strips/asr/segments")
    assert out.cooked and out.outcome == "exact" and out.reproducibility == "exact" and out.measured
    assert out.node.content_hash() == asr_hash
    # downstream still cached: same input hash, same cache key
    again = await g.produce(M, "claims/L2#w1")
    assert again.cache_hit
    # prune the nondeterministic one: rebuilt differently, equivalent, and consumers recook
    store.prune(diar_hash)
    out = await g.produce(M, "claims/L2#w1")
    assert out.cooked
    diar = await g.produce(M, "strips/diar/segments")
    assert diar.cache_hit and diar.reproducibility == "equivalent" and diar.measured
    assert store.get(store.current(M), "strips/diar/segments").content_hash() != diar_hash
    assert out.node.data["claims"] == first.node.data["claims"]  # the claims happened to agree; the receipt does not claim exactness


@pytest.mark.asyncio
async def test_a_realization_that_lies_about_determinism_is_flagged():
    store = Store()
    tx = store.transaction(LIB)
    tx.put(template("liar", LIAR.handle))
    tx.commit()
    tx = store.transaction(M)
    tx.put(Node(id="seed", kind="k", scope=M, data=1))
    tx.put(Node(id="out", kind="k", scope=M, edges=(Edge(target="seed", role="x"), recipe("liar"))))
    tx.commit()
    g = Generator(store, INVENTORY)
    first = await g.produce(M, "out")
    assert first.reproducibility == "exact" and not first.measured and not first.falsified_determinism
    store.prune(first.node.content_hash())
    second = await g.produce(M, "out")
    assert second.cooked and second.measured and second.falsified_determinism
    assert second.reproducibility == "equivalent"
    assert store.get(store.current(M), envelope_id("out")).data["falsified_determinism"] is True


@pytest.mark.asyncio
async def test_lost_names_the_unrecoverable_reference():
    store = Store()
    build_graph(store)
    g = Generator(store, INVENTORY)
    store.prune(store.get(store.current(M), "audio.wav").content_hash())  # a source, no envelope
    out = await g.produce(M, "claims/L2#w1")
    assert out.outcome == "lost" and out.lost == (f"{M}:audio.wav",)
    assert store.get(store.current(M), "claims/L2#w1").data is None  # nothing was produced


@pytest.mark.asyncio
async def test_missing_realization_fails_ordinarily():
    store = Store()
    build_graph(store)
    g = Generator(store, [DIAR, TURNS, CLAIMS])  # no asr
    with pytest.raises(NoRealization, match="mock.asr@1"):
        await g.produce(M, "claims/L2#w1")


# --- dispatch: a plan's assignments are bindings ------------------------------------


def _plan(assignments):
    zero = CostVector(latency=0.0, energy=0.0, money=0.0)
    return Plan(
        graph_hash="g",
        policy_hash="p",
        method="region",
        assignments=tuple(assignments),
        regions=(("air", tuple(a.node_id for a in assignments)),),
        crossings=0,
        compute=zero,
        boundary=zero,
        heuristic=zero,
        objective=0.0,
        compute_cost=0.0,
        boundary_cost=0.0,
        excluded={},
    )


@pytest.mark.asyncio
async def test_dispatching_a_plan_binds_realizations_and_records_the_plan():
    store = Store()
    build_graph(store)
    zero = CostVector(latency=0.0, energy=0.0, money=0.0)
    plan = _plan(
        [
            Assignment(node_id="strips/asr/segments", executor_id="air", realization=ASR_FAST.handle, compute=zero, reason="cheapest passing"),
            Assignment(node_id="strips/diar/segments", executor_id="air", realization=DIAR.handle, compute=zero, reason="only"),
            Assignment(node_id="strips/turns", executor_id="air", realization=TURNS.handle, compute=zero, reason="only"),
            Assignment(node_id="claims/L2#w1", executor_id="dgx", realization=CLAIMS.handle, compute=zero, reason="only"),
        ]
    )
    g = Generator(store, INVENTORY)
    out = await g.dispatch(plan, M)
    asr = out["strips/asr/segments"]
    assert asr.outcome == "via-substitute" and asr.realization == ASR_FAST.handle and asr.declared_realization == ASR.handle
    assert out["claims/L2#w1"].outcome == "exact"
    assert out["claims/L2#w1"].node.data["claims"][0] == "MEETING.WAV-SEG0"  # the substitute's output flowed downstream
    env = store.get(store.current(M), envelope_id("strips/asr/segments")).data
    assert env["recipe"]["plan"] == plan.content_hash() and env["recipe"]["executor"] == "air"
    assert env["recipe"]["declared"] == ASR.handle and env["recipe"]["realization"] == ASR_FAST.handle
    # producing without the plan afterwards recooks with the declared realization: a different cache key
    plain = Generator(store, INVENTORY)
    back = await plain.produce(M, "strips/asr/segments")
    assert back.cooked and back.outcome == "exact"


# --- patterns -----------------------------------------------------------------------


def test_select_applies_patterns_and_demands_a_window_for_ranges():
    els = [{"t": {"start_ms": 0, "end_ms": 1000}}, {"t": {"start_ms": 1000, "end_ms": 2000}}, {"t": {"start_ms": 2000, "end_ms": 3000}}]
    from nodecules.core.strip_access import AllPattern

    assert select(AllPattern(), els, None) == els
    assert select(LatestPattern(), els, None) == els[-1]
    assert select(LatestPattern(), {"x": 1}, None) == {"x": 1}
    rp = RangePattern(field="t", start=SelfWindowStart(), end=SelfWindowEnd())
    assert select(rp, els, TimeRange(start_ms=0, end_ms=1500)) == els[:2]
    with pytest.raises(ValueError, match="window"):
        select(rp, els, None)
