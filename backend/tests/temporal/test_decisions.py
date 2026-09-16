"""Decisions and presentations: one decision, several presentations, one
outcome; and deferral of a step that needs a model nobody here has."""

from __future__ import annotations

import pytest

from nodecules.core.decisions import Decision, Option, advance_realization, derive, presentation_realization, step_element
from nodecules.core.deferral import fulfill, requests
from nodecules.core.generation import PARAMS_ROLE, RECIPE_ROLE, RECIPE_TEMPLATE_KIND, Generator
from nodecules.core.store import Edge, Node, Store, envelope_id
from nodecules.core.strip_access import AllPattern
from nodecules.core.strip_nodes import strip_append

BIZ = "trip/plan"
LIB = "lib"
OPTIONS = tuple(Option(id=f"o{i}", label=f"Option {i}", facts={"price": 100 + i, "days": 3 + i % 4}) for i in range(16))
DEC = Decision(id="dest", prompt="Where should we go?", options=OPTIONS, needs=("price", "days"), stakes="a week of vacation")


def test_derive_shows_everything_at_once_on_a_big_surface_and_a_tournament_on_a_small_one():
    big = derive(DEC, [], max_options=16)
    assert big.mode == "all-at-once" and big.step.id == "choose" and len(big.step.options) == 16 and not big.done
    done = derive(DEC, [{"step": "choose", "chosen": "o7"}], max_options=16)
    assert done.done and done.chosen == "o7" and done.path == ({"step": "choose", "chosen": "o7"},)
    small = derive(DEC, [], max_options=4)
    assert small.mode == "tournament" and small.step.id == "r1g0" and [o.id for o in small.step.options] == ["o0", "o1", "o2", "o3"]
    assert small.step.of == 4 and small.step.index == 0
    # four round-one answers, then the final of four winners
    answers = [{"step": f"r1g{g}", "chosen": f"o{g * 4 + 1}"} for g in range(4)]
    final = derive(DEC, answers, max_options=4)
    assert final.step.id == "final" and [o.id for o in final.step.options] == ["o1", "o5", "o9", "o13"]
    won = derive(DEC, answers + [{"step": "final", "chosen": "o9"}], max_options=4)
    assert won.done and won.chosen == "o9" and len(won.path) == 5
    # an answer for the wrong step is rejected, not applied, and the step does not move
    wrong = derive(DEC, [{"step": "r1g2", "chosen": "o9"}], max_options=4)
    assert wrong.step.id == "r1g0" and wrong.rejected_answers == ({"step": "r1g2", "chosen": "o9"},)


def test_a_non_visual_presentation_of_the_same_decision_reaches_the_same_outcome():
    spoken = derive(DEC, [], max_options=4, non_visual=True)
    assert spoken.modality == "audio" and spoken.spoken.startswith("Where should we go? (round 1, group 1 of 4). Say 1 for Option 0")
    el = step_element(spoken, present_at=0, expires_at=1000)
    assert el.payload["kind"] == "choice" and el.payload["spoken"] and el.payload["modality"] == "audio"
    assert [o["id"] for o in el.payload["options"]] == ["o0", "o1", "o2", "o3"]
    answers = [{"step": f"r1g{g}", "chosen": f"o{g * 4}"} for g in range(4)] + [{"step": "final", "chosen": "o12"}]
    assert derive(DEC, answers, max_options=4, non_visual=True).chosen == derive(DEC, answers, max_options=4).chosen == "o12"
    assert step_element(derive(DEC, answers, max_options=4), present_at=0, expires_at=1) is None  # done: nothing to show
    assert DEC.missing_facts() == {}
    assert Decision(id="d", prompt="p", options=(Option(id="a", label="A"),), needs=("price",)).missing_facts() == {"a": ["price"]}


def _graph(store: Store, max_options: int) -> None:
    tx = store.transaction(LIB, author="dev")
    tx.put(Node(id="recipes/present", kind=RECIPE_TEMPLATE_KIND, scope=LIB, data={"realization": "present.choice@1", "params": {}}))
    tx.put(Node(id="recipes/advance", kind=RECIPE_TEMPLATE_KIND, scope=LIB, data={"realization": "decision.advance@1", "params": {}}))
    tx.commit()
    tx = store.transaction(BIZ, author="butler")
    tx.put(Node(id="decisions/dest", kind="decision", scope=BIZ, data=DEC.model_dump(mode="json")))
    tx.put(Node(id="surfaces/phone", kind="params", scope=BIZ, data={"max_options": max_options}))
    tx.put(
        Node(
            id="present/dest@phone",
            kind="presentation",
            scope=BIZ,
            edges=(
                Edge(target="decisions/dest", role="decision"),
                Edge(target="strips/answers/alice", pattern=AllPattern(), role="answers", optional=True),
                Edge(target="recipes/present", scope=LIB, role=RECIPE_ROLE),
                Edge(target="surfaces/phone", role=PARAMS_ROLE),
            ),
        )
    )
    tx.put(Node(id="plan/next", kind="business.step", scope=BIZ, edges=(Edge(target="present/dest@phone", role="choice"), Edge(target="recipes/advance", scope=LIB, role=RECIPE_ROLE))))
    tx.commit("declare")


@pytest.mark.asyncio
async def test_business_graph_advances_exactly_once_as_answers_land_on_the_input_strip():
    store = Store()
    _graph(store, max_options=4)
    g = Generator(store, [presentation_realization(), advance_realization()])
    out = await g.produce(BIZ, "plan/next")
    assert out.node.data == {"advanced": False, "waiting_on": "r1g0"}
    present = store.get(store.current(BIZ), "present/dest@phone")
    assert present.data["step"]["id"] == "r1g0"
    advanced_cooks = 0
    for step, pick in [("r1g0", "o2"), ("r1g1", "o5"), ("r1g2", "o8"), ("r1g3", "o15"), ("final", "o8")]:
        strip_append(store, BIZ, "strips/answers/alice", {"step": step, "chosen": pick}, author="alice")
        out = await g.produce(BIZ, "plan/next")
        if out.cooked and out.node.data.get("advanced"):
            advanced_cooks += 1
    assert advanced_cooks == 1
    assert out.node.data["chosen"] == "o8" and out.node.data["decision"] == "dest"
    # asking again changes nothing
    again = await g.produce(BIZ, "plan/next")
    assert again.cache_hit
    # the receipt of the presentation names the answers strip it read
    env = store.get(store.current(BIZ), envelope_id("present/dest@phone")).data
    assert f"{BIZ}:strips/answers/alice" in env["inputs"]


def _declare(store: Store) -> None:
    tx = store.transaction(LIB, author="dev")
    tx.put(Node(id="recipes/consider", kind=RECIPE_TEMPLATE_KIND, scope=LIB, data={"realization": "llm.spark@1", "params": {"system": "Summarise each option in one line."}}))
    tx.put(Node(id="recipes/advance", kind=RECIPE_TEMPLATE_KIND, scope=LIB, data={"realization": "decision.advance@1", "params": {}}))
    tx.commit()
    tx = store.transaction(BIZ, author="butler")
    tx.put(Node(id="decisions/dest", kind="decision", scope=BIZ, data=DEC.model_dump(mode="json")))
    tx.put(Node(id="consider/dest", kind="llm.consideration", scope=BIZ, edges=(Edge(target="decisions/dest", role="decision"), Edge(target="recipes/consider", scope=LIB, role=RECIPE_ROLE))))
    tx.put(Node(id="plan/next", kind="business.step", scope=BIZ, edges=(Edge(target="consider/dest", role="choice"), Edge(target="recipes/advance", scope=LIB, role=RECIPE_ROLE))))
    tx.commit("declare")


@pytest.mark.asyncio
async def test_a_step_needing_a_model_nobody_here_has_becomes_a_request_that_something_else_fulfils():
    store = Store()
    _declare(store)
    here = Generator(store, [advance_realization()], defer=True)  # no model in this inventory
    out = await here.produce(BIZ, "plan/next")
    assert out.outcome == "pending" and "consider/dest" in out.note
    pending = requests(store, BIZ)
    assert len(pending) == 1 and pending[0]["for"] == "consider/dest" and pending[0]["realization"] == "llm.spark@1"
    assert f"{BIZ}:decisions/dest" in pending[0]["inputs"] and pending[0]["params"]["system"].startswith("Summarise")
    # asking again does not write a second request
    seq = store.current(BIZ).seq
    again = await here.produce(BIZ, "plan/next")
    assert again.outcome == "pending" and store.current(BIZ).seq == seq
    # elsewhere, something with the model reads the request, cooks, and fulfils it
    answer = {"done": True, "chosen": "o3", "decision": "dest", "summaries": {"o3": "cheapest with four days"}}
    fulfill(store, BIZ, "consider/dest", answer, by="spark-agent", realization="llm.spark@1")
    assert requests(store, BIZ) == []
    env = store.get(store.current(BIZ), envelope_id("consider/dest")).data
    assert env["recipe"]["fulfilled_by"] == "spark-agent" and env["cache_key"] == pending[0]["cache_key"]
    # back here: the consideration is a cache hit and the business graph advances
    out = await here.produce(BIZ, "plan/next")
    assert out.cooked and out.node.data["advanced"] and out.node.data["chosen"] == "o3"
    assert (await here.produce(BIZ, "consider/dest")).cache_hit


@pytest.mark.asyncio
async def test_a_producer_that_has_the_realization_clears_the_request_it_answers():
    """The other way to fulfil: run the generator with the missing realization
    in inventory (what `handoff/fulfil.py` does). The request goes in the
    same commit as the answer, and the envelope names who fulfilled it."""
    from nodecules.core.generation import Realization
    from nodecules.core.store import envelope_id

    store = Store()
    _declare(store)
    here = Generator(store, [advance_realization()], defer=True)
    out = await here.produce(BIZ, "consider/dest")
    assert out.outcome == "pending" and len(requests(store, BIZ)) == 1

    async def cook(inputs, params):
        return {"summaries": {o["id"]: o["label"] for o in inputs["decision"]["options"]}}

    spark = Generator(store, [advance_realization(), Realization(handle="llm.spark@1", cook=cook)], author="spark", defer=True)
    done = await spark.produce(BIZ, "consider/dest")
    assert done.cooked and done.outcome == "exact"
    assert requests(store, BIZ) == []
    m = store.current(BIZ)
    env = store.get(m, envelope_id("consider/dest")).data
    assert env["recipe"]["fulfilled_by"] == "spark" and env["recipe"]["request"] == "requests/consider/dest"
    assert m.author == "spark" and m.note.startswith("fulfil consider/dest")
    back = await here.produce(BIZ, "consider/dest")
    assert back.cache_hit and back.node.data["summaries"]["o9"] == "Option 9"
