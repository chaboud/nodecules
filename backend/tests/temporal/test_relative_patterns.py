"""Relative and range-relative reads (founder, 2026-10-10: "live observation
like keyhole never works without relative relationships"). They resolve at
production time against the strip's head or the reader's own position and
window, only look back, record what they resolved to in the receipt, put
what they read (not the whole strip) in the cache key, and come back lost,
naming the floor, when they reach into elements a strip released."""

from __future__ import annotations

import pytest
from pydantic import ValidationError

from nodecules.core.generation import PARAMS_ROLE, RECIPE_ROLE, RECIPE_TEMPLATE_KIND, Generator, Realization, read
from nodecules.core.store import Edge, Node, Store, envelope_id
from nodecules.core.strip_access import Ago, AllPattern, FromHead, FromSelf, Index, LatestPattern, OrdinalPattern, RangePattern, SelfWindowEnd, last, previous
from nodecules.core.strip_nodes import strip_append, strip_floor, strip_len, strip_read, strip_trim
from nodecules.core.time import TimeRange

S, LIB = "obs", "lib"


def _obs(t0: int, t1: int, what: str) -> dict:
    return {"ts": {"start_ms": t0, "end_ms": t1}, "what": what}


def _declare(store: Store, readers) -> list:
    """readers: node id -> (pattern, params or None). One recipe that returns what it was given."""
    calls = []

    async def echo(inputs, params):
        calls.append(inputs.get("seen"))
        return {"seen": inputs.get("seen")}

    tx = store.transaction(LIB, author="dev")
    tx.put(Node(id="r/echo", kind=RECIPE_TEMPLATE_KIND, scope=LIB, data={"realization": "echo@1", "params": {}}))
    tx.commit()
    tx = store.transaction(S, author="dev")
    for nid, (pattern, params) in readers.items():
        edges = [Edge(target="strips/obs", pattern=pattern, role="seen"), Edge(target="r/echo", scope=LIB, role=RECIPE_ROLE)]
        if params is not None:
            tx.put(Node(id=f"params/{nid}", kind="params", scope=S, data=params))
            edges.append(Edge(target=f"params/{nid}", role=PARAMS_ROLE))
        tx.put(Node(id=nid, kind="k", scope=S, edges=tuple(edges)))
    tx.commit()
    return calls, Realization("echo@1", echo)


def test_the_patterns_only_look_back():
    with pytest.raises(ValidationError):
        FromHead(offset=1)
    with pytest.raises(ValidationError):
        FromSelf(offset=2)
    with pytest.raises(ValidationError):
        Ago(anchor="head", ms=-5)
    with pytest.raises(ValueError):
        last(0)
    assert last(3) == OrdinalPattern(start=FromHead(offset=-3), end=FromHead())
    assert previous() == OrdinalPattern(start=FromSelf(offset=-1), end=FromSelf())


def test_ordinal_reads_by_count_relative_to_the_head_or_the_reader():
    els = [0, 1, 2, 3, 4, 5]
    assert read(last(3), els, None).value == [3, 4, 5]
    assert read(last(10), els, None).value == els  # before the first element: clipped, nothing was ever there
    assert read(previous(), els, None, position=4).value == [3]
    assert read(previous(2), els, None, position=1).value == [0]
    assert read(previous(), els, None, position=0).value == []  # the first element has no previous
    assert read(OrdinalPattern(start=Index(index=2), end=Index(index=4)), els, None).value == [2, 3]
    r = read(previous(), els, None, position=9)
    assert r.waiting == "element 8 does not exist yet (the strip has 6)" and r.value is None
    with pytest.raises(ValueError, match="relative to the reader's position"):
        read(previous(), els, None)
    assert read(last(2), els, None).resolved == {"pattern": "ordinal", "start": 4, "end": 6, "of": 6, "count": 2}


def test_time_reads_relative_to_the_strip_head_need_no_clock():
    els = [_obs(0, 1000, "a"), _obs(4000, 5000, "b"), _obs(9000, 10000, "c"), _obs(10000, 11000, "d")]
    recent = RangePattern(field="ts", start=Ago(anchor="head", ms=3000), end=Ago(anchor="head"))
    r = read(recent, els, None)
    assert [e["what"] for e in r.value] == ["c", "d"] and r.resolved["window"] == [8000, 11000]
    assert read(recent, [], None).value == []  # an empty strip has no head and nothing recent
    mine = RangePattern(field="ts", start=Ago(anchor="self_window_end", ms=6000), end=SelfWindowEnd())
    assert [e["what"] for e in read(mine, els, TimeRange(start_ms=9000, end_ms=10000)).value] == ["b", "c"]


@pytest.mark.asyncio
async def test_the_cache_key_covers_what_was_read_so_an_append_outside_a_window_is_a_hit():
    store = Store()
    strip_append(store, S, "strips/obs", _obs(0, 1000, "a"), _obs(1000, 2000, "b"), author="sensor")
    calls, echo = _declare(store, {
        "early": (RangePattern(field="ts", start=Ago(anchor="self_window_start"), end=SelfWindowEnd()), {"window": {"start_ms": 0, "end_ms": 1500}}),
        "everything": (AllPattern(), None),
        "recent": (last(1), None),
    })
    g = Generator(store, [echo])
    for nid in ("early", "everything", "recent"):
        assert (await g.produce(S, nid)).cooked
    strip_append(store, S, "strips/obs", _obs(5000, 6000, "c"), author="sensor")  # after the early window
    early = await g.produce(S, "early")
    assert early.cache_hit and not early.cooked  # what it reads did not change
    assert (await g.produce(S, "everything")).cooked  # a whole read sees every append
    assert (await g.produce(S, "recent")).node.data == {"seen": [_obs(5000, 6000, "c")]}
    strip_append(store, S, "strips/obs", _obs(500, 700, "late arrival inside the window"), author="sensor")
    assert (await g.produce(S, "early")).cooked
    assert [r.read_hash is None for r in (await g.produce(S, "everything")).inputs if r.name == "strips/obs"] == [True]


@pytest.mark.asyncio
async def test_the_receipt_records_what_a_relative_read_resolved_to():
    store = Store()
    strip_append(store, S, "strips/obs", *[_obs(i * 1000, i * 1000 + 500, str(i)) for i in range(6)], author="sensor")
    calls, echo = _declare(store, {"tail": (last(2), None), "before": (previous(2), {"position": 3})})
    g = Generator(store, [echo])
    await g.produce(S, "tail")
    await g.produce(S, "before")
    m = store.current(S)
    assert store.get(m, envelope_id("tail")).data["recipe"]["reads"] == {"obs:strips/obs": {"pattern": "ordinal", "start": 4, "end": 6, "of": 6, "count": 2}}
    assert store.get(m, envelope_id("before")).data["recipe"]["reads"]["obs:strips/obs"]["start"] == 1
    assert [e["what"] for e in store.get(m, "before").data["seen"]] == ["1", "2"]


@pytest.mark.asyncio
async def test_a_read_into_released_elements_is_lost_and_names_the_floor():
    store = Store()
    strip_append(store, S, "strips/obs", *[_obs(i * 1000, i * 1000 + 500, str(i)) for i in range(10)], author="sensor")
    strip_trim(store, S, "strips/obs", keep_last=4, author="janitor")
    m = store.current(S)
    assert strip_floor(store, m, "strips/obs") == 6 and strip_len(store, m, "strips/obs") == 10
    assert [e["what"] for e in strip_read(store, m, "strips/obs")] == ["6", "7", "8", "9"]
    strip_append(store, S, "strips/obs", _obs(10000, 10500, "10"), author="sensor")
    assert strip_floor(store, store.current(S), "strips/obs") == 6 and strip_len(store, store.current(S), "strips/obs") == 11  # the floor survives appends

    calls, echo = _declare(store, {
        "all": (AllPattern(), None),
        "tail": (last(3), None),
        "deep": (last(8), None),
        "near": (previous(1), {"position": 9}),
        "far": (previous(1), {"position": 3}),
        "window_ok": (RangePattern(field="ts", start=Ago(anchor="head", ms=2000), end=Ago(anchor="head")), None),
        "window_old": (RangePattern(field="ts", start=Ago(anchor="head", ms=9000), end=Ago(anchor="head")), None),
        "latest": (LatestPattern(), None),
    })
    g = Generator(store, [echo])
    res = await g.produce_many([(S, n) for n in ("all", "tail", "deep", "near", "far", "window_ok", "window_old", "latest")])
    lost = {n for (sc, n), r in res.items() if r.outcome == "lost"}
    assert lost == {"all", "deep", "far", "window_old"}
    assert "an All read needs every element, and elements [0, 6) were released" in res[(S, "all")].note
    assert "elements [3, 6) were released (the floor is 6)" in res[(S, "deep")].note
    assert "elements [8, 9) were released" not in res[(S, "near")].note and res[(S, "near")].node.data["seen"][0]["what"] == "8"
    assert "before the earliest retained element (6000)" in res[(S, "window_old")].note
    assert [e["what"] for e in res[(S, "tail")].node.data["seen"]] == ["8", "9", "10"]
    assert res[(S, "latest")].node.data["seen"]["what"] == "10"
    assert res[(S, "deep")].lost == ("obs:strips/obs",)
    old = store.history(S)  # the release is a new version; an old manifest still sees every element
    assert any(len(strip_read(store, mm, "strips/obs")) == 10 and strip_floor(store, mm, "strips/obs") == 0 for mm in old)


@pytest.mark.asyncio
async def test_a_read_waiting_for_an_element_is_pending_and_a_reactor_produces_it_when_the_element_arrives():
    from nodecules.core.reactor import Reactor

    store = Store()
    strip_append(store, S, "strips/obs", _obs(0, 500, "0"), author="sensor")
    calls, echo = _declare(store, {"third": (OrdinalPattern(start=Index(index=2), end=Index(index=3)), None)})
    r = Reactor(Generator(store, [echo], author="reactor"), [(S, "third")]).start()
    await r.settle()
    assert r.last[(S, "third")].outcome == "pending" and "element 2 does not exist yet (the strip has 1)" in r.last[(S, "third")].note
    strip_append(store, S, "strips/obs", _obs(1000, 1500, "1"), author="sensor")
    await r.settle()
    assert r.last[(S, "third")].outcome == "pending"
    strip_append(store, S, "strips/obs", _obs(2000, 2500, "2"), author="sensor")
    await r.settle()
    assert r.last[(S, "third")].cooked and r.last[(S, "third")].node.data["seen"][0]["what"] == "2"
    strip_append(store, S, "strips/obs", _obs(3000, 3500, "3"), author="sensor")
    await r.settle()
    assert r.last[(S, "third")].cache_hit and len(calls) == 1  # later appends do not change element 2


def test_the_live_observation_demo_runs_and_shows_the_floor():
    import os
    import subprocess
    import sys
    from pathlib import Path

    here = Path(__file__).resolve().parents[2]
    out = subprocess.run([sys.executable, str(here.parent / "demos" / "live_observation.py")], capture_output=True, text=True, timeout=60, env={**os.environ, "PYTHONPATH": str(here)})
    assert out.returncode == 0, out.stderr
    assert "t=5s  in view: ball, cup, dog" in out.stdout and "left: person" in out.stdout
    assert "'window': [6000, 9000]" in out.stdout
    assert "history: lost" in out.stdout and "elements [0, 6) were released" in out.stdout
    assert "nobody called produce" in out.stdout
