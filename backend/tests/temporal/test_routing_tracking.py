"""Routing as graph structure, failure as a state, and tracking outside the
store."""

from __future__ import annotations

from pathlib import Path

import pytest

from nodecules.core.generation import PARAMS_ROLE, RECIPE_ROLE, RECIPE_TEMPLATE_KIND, Generator, Realization, router_realization
from nodecules.core.store import Edge, Node, Store, envelope_id
from nodecules.core.tracking import JsonlSink, Tracker, lineage

M = "m/1"
LIB = "lib"


async def double(inputs, params):
    return {"v": inputs["x"]["v"] * 2}


class Flaky:
    calls = 0


async def flaky(inputs, params):
    Flaky.calls += 1
    if Flaky.calls < 2:
        raise RuntimeError("transient")
    return {"ok": True}


async def broken(inputs, params):
    raise ValueError("bad recipe")


INV = [Realization("double@1", double, deterministic=True), Realization("flaky@1", flaky), Realization("broken@1", broken), router_realization()]


def _lib(store: Store, **templates) -> None:
    tx = store.transaction(LIB, author="dev")
    for name, (handle, params) in templates.items():
        tx.put(Node(id=f"recipes/{name}", kind=RECIPE_TEMPLATE_KIND, scope=LIB, data={"realization": handle, "params": params}))
    tx.commit()


def _recipe(name: str) -> Edge:
    return Edge(target=f"recipes/{name}", scope=LIB, role=RECIPE_ROLE)


@pytest.mark.asyncio
async def test_router_picks_the_primary_when_present_and_the_fallback_when_not():
    store = Store()
    _lib(store, route=("router.first-available@1", {"prefer": ["fast", "slow"]}), dbl=("double@1", {}))
    tx = store.transaction(M)
    tx.put(Node(id="fast", kind="k", scope=M, data={"v": 1}))
    tx.put(Node(id="slow", kind="k", scope=M, data={"v": 100}))
    tx.put(Node(id="pick", kind="router", scope=M, edges=(Edge(target="fast", role="fast", optional=True), Edge(target="slow", role="slow", optional=True), _recipe("route"))))
    tx.put(Node(id="out", kind="k", scope=M, edges=(Edge(target="pick", role="x"), _recipe("dbl"))))
    tx.commit()
    g = Generator(store, INV)
    out = await g.produce(M, "out")
    assert out.node.data == {"v": 2}
    pick = store.get(store.current(M), envelope_id("pick")).data
    assert pick["recipe"]["route"] == {"chose": "fast", "primary": "fast"} and pick["outcome"] == "exact"
    # the fast path goes away: the router substitutes, visibly, and downstream recooks
    store.prune(store.current(M).hash_of("fast"))
    out = await g.produce(M, "out")
    assert out.node.data == {"v": 200}
    pick = store.get(store.current(M), envelope_id("pick")).data
    assert pick["outcome"] == "via-substitute" and pick["recipe"]["route"]["chose"] == "slow"
    assert pick["recipe"]["omitted"] == [f"{M}:fast"]
    # nothing available: the router fails ordinarily, and so does its consumer
    store.prune(store.current(M).hash_of("slow"))
    out = await g.produce(M, "out")
    assert out.outcome == "failed" and "no alternative" in (out.error or "")


@pytest.mark.asyncio
async def test_a_failing_realization_is_a_state_not_a_crash_and_retry_is_a_param():
    store = Store()
    _lib(store, bad=("broken@1", {}), retry=("flaky@1", {"retry": 2}))
    tx = store.transaction(M)
    tx.put(Node(id="seed", kind="k", scope=M, data={"v": 1}))
    tx.put(Node(id="bad", kind="k", scope=M, edges=(Edge(target="seed", role="x"), _recipe("bad"))))
    tx.put(Node(id="eventually", kind="k", scope=M, edges=(Edge(target="seed", role="x"), _recipe("retry"))))
    m0 = tx.commit()
    g = Generator(store, INV)
    out = await g.produce(M, "bad")
    assert out.outcome == "failed" and out.error == "ValueError: bad recipe" and out.attempts == 1
    assert store.current(M) is m0  # nothing was committed
    Flaky.calls = 0
    out = await g.produce(M, "eventually")
    assert out.cooked and out.attempts == 2 and out.node.data == {"ok": True}
    assert store.get(store.current(M), envelope_id("eventually")).data["recipe"]["attempts"] == 2


@pytest.mark.asyncio
async def test_tracker_records_commits_head_moves_and_productions_and_sinks_to_jsonl(tmp_path: Path):
    ticks = iter(range(100, 10_000, 7))
    tracker = Tracker(clock=lambda: next(ticks), timeline="table", sink=JsonlSink(tmp_path / "events.jsonl"))
    store = Store()
    tracker.attach(store)
    _lib(store, dbl=("double@1", {}))
    tx = store.transaction(M, author="alice")
    tx.put(Node(id="seed", kind="k", scope=M, data={"v": 3}))
    tx.put(Node(id="out", kind="k", scope=M, edges=(Edge(target="seed", role="x"), _recipe("dbl"))))
    tx.commit("declare")
    g = Generator(store, INV, tracker=tracker)
    await g.produce(M, "out")
    await g.produce(M, "out")
    kinds = [e.kind for e in tracker.events(scope=M)]
    assert kinds[:2] == ["commit", "produce.cache-hit"] or kinds[0] == "commit"
    assert [e.kind for e in tracker.events(kind="produce", node="out")] == ["produce.cooked", "produce.cache-hit"]
    cooked = tracker.events(kind="produce.cooked")[0]
    assert cooked.detail["realization"] == "double@1" and cooked.at is not None and cooked.node == "out"
    commits = tracker.events(kind="commit", scope=M)
    assert commits[0].detail["author"] == "alice" and commits[0].detail["note"] == "declare"
    assert commits[-1].detail["author"] == "generator" and commits[-1].detail["note"] == "produce out"
    lines = (tmp_path / "events.jsonl").read_text().strip().splitlines()
    assert len(lines) == len(tracker)


@pytest.mark.asyncio
async def test_lineage_walks_receipts_back_to_sources():
    store = Store()
    _lib(store, dbl=("double@1", {}))
    tx = store.transaction(M)
    tx.put(Node(id="seed", kind="k", scope=M, data={"v": 3}))
    tx.put(Node(id="mid", kind="k", scope=M, edges=(Edge(target="seed", role="x"), _recipe("dbl"))))
    tx.put(Node(id="top", kind="k", scope=M, edges=(Edge(target="mid", role="x"), _recipe("dbl"))))
    tx.commit()
    await Generator(store, INV).produce(M, "top")
    tree = lineage(store, store.current(M), "top")
    assert tree["receipt"]["outcome"] == "exact" and tree["recipe"]["realization"] == "double@1"
    assert [i["id"] for i in tree["inputs"]] == ["mid"]
    assert [i["id"] for i in tree["inputs"][0]["inputs"]] == ["seed"]
    assert "receipt" not in tree["inputs"][0]["inputs"][0]  # a source has no receipt
