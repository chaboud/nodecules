"""Concurrent production: independent branches cook at the same time; a
realization can cap its own concurrency; two calls that need one node share
one cook; a failing branch does not stop the others."""

from __future__ import annotations

import asyncio

import pytest

from nodecules.core.generation import RECIPE_ROLE, RECIPE_TEMPLATE_KIND, Generator, Realization
from nodecules.core.store import Edge, Node, Store

S, LIB = "work", "lib"


def _declare(store: Store, recipes, nodes) -> None:
    tx = store.transaction(LIB, author="dev")
    for rid, handle in recipes.items():
        tx.put(Node(id=rid, kind=RECIPE_TEMPLATE_KIND, scope=LIB, data={"realization": handle, "params": {}}))
    tx.commit()
    tx = store.transaction(S, author="dev")
    tx.put(Node(id="src", kind="source", scope=S, data={"x": 1}))
    for nid, (recipe, inputs) in nodes.items():
        tx.put(Node(id=nid, kind="k", scope=S, edges=tuple(Edge(target=i, role=i) for i in inputs) + (Edge(target=recipe, scope=LIB, role=RECIPE_ROLE),)))
    tx.commit()


@pytest.mark.asyncio
async def test_independent_branches_cook_at_the_same_time():
    """Each branch waits for the other to have started. Cooked one after the
    other, the first would time out; cooked together, both finish, and the
    node that reads both cooks after them."""
    a_started, b_started = asyncio.Event(), asyncio.Event()
    seen = []

    async def a(inputs, params):
        a_started.set()
        await asyncio.wait_for(b_started.wait(), 2)
        seen.append("a")
        return {"from": "a"}

    async def b(inputs, params):
        b_started.set()
        await asyncio.wait_for(a_started.wait(), 2)
        seen.append("b")
        return {"from": "b"}

    async def join(inputs, params):
        seen.append("join")
        return {"both": [inputs["left"]["from"], inputs["right"]["from"]]}

    store = Store()
    _declare(store, {"r/a": "a@1", "r/b": "b@1", "r/join": "join@1"}, {"left": ("r/a", ["src"]), "right": ("r/b", ["src"]), "out": ("r/join", ["left", "right"])})
    g = Generator(store, [Realization("a@1", a), Realization("b@1", b), Realization("join@1", join)])
    out = await g.produce(S, "out")
    assert out.cooked and out.outcome == "exact" and out.node.data == {"both": ["a", "b"]}
    assert sorted(seen[:2]) == ["a", "b"] and seen[2] == "join"
    assert g.cooks == 3


@pytest.mark.asyncio
async def test_a_realization_capped_at_one_cooks_one_at_a_time_and_an_uncapped_one_does_not():
    async def run(cap):
        inflight = {"now": 0, "max": 0}

        async def slow(inputs, params):
            inflight["now"] += 1
            inflight["max"] = max(inflight["max"], inflight["now"])
            await asyncio.sleep(0.01)
            inflight["now"] -= 1
            return {"ok": True}

        store = Store()
        _declare(store, {"r/slow": "slow@1"}, {f"n{i}": ("r/slow", ["src"]) for i in range(4)})
        g = Generator(store, [Realization("slow@1", slow, concurrency=cap)])
        res = await g.produce_many([(S, f"n{i}") for i in range(4)])
        assert all(r.cooked for r in res.values()) and g.cooks == 4
        return inflight["max"]

    assert await run(1) == 1  # a model server with one slot, or one shared with keyhole
    assert await run(None) == 4


@pytest.mark.asyncio
async def test_two_calls_needing_the_same_node_share_one_cook():
    gate = asyncio.Event()

    async def slow(inputs, params):
        await gate.wait()
        return {"v": 1}

    store = Store()
    _declare(store, {"r/slow": "slow@1"}, {"n": ("r/slow", ["src"])})
    g = Generator(store, [Realization("slow@1", slow)])
    first = asyncio.ensure_future(g.produce(S, "n"))
    second = asyncio.ensure_future(g.produce(S, "n"))
    await asyncio.sleep(0)
    gate.set()
    one, two = await asyncio.gather(first, second)
    assert g.cooks == 1 and one is two  # the second call awaited the first's production


@pytest.mark.asyncio
async def test_a_failing_branch_does_not_stop_the_other_and_its_consumer_names_the_cause():
    async def boom(inputs, params):
        raise RuntimeError("engine down")

    async def fine(inputs, params):
        await asyncio.sleep(0)
        return {"ok": True}

    async def join(inputs, params):
        return {"never": True}

    store = Store()
    _declare(store, {"r/boom": "boom@1", "r/fine": "fine@1", "r/join": "join@1"}, {"left": ("r/boom", ["src"]), "right": ("r/fine", ["src"]), "out": ("r/join", ["left", "right"])})
    g = Generator(store, [Realization("boom@1", boom), Realization("fine@1", fine), Realization("join@1", join)])
    res = await g.produce_many([(S, "out"), (S, "right")])
    assert res[(S, "right")].cooked and res[(S, "right")].outcome == "exact"
    assert res[(S, "out")].outcome == "failed" and "work:left failed: RuntimeError: engine down" in res[(S, "out")].error
    assert g.cooks == 1
