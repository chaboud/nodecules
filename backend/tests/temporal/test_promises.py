"""A read is a promise (vault ADR-0038). It asks for exactly what it names or
for the nearest thing that exists; the answer is a hit, or a miss with a
typed reason: not-yet (pending; nothing waits), or a final one (released,
ended, gap). A nearest hit records its distance and counts as a
substitution; when the exact thing can still arrive it is provisional, wet
ink, and so is everything downstream of it until the exact lands."""

from __future__ import annotations

import asyncio

import pytest

from nodecules.core.generation import PARAMS_ROLE, RECIPE_ROLE, RECIPE_TEMPLATE_KIND, Generator, Realization, read
from nodecules.core.store import Cycle, Edge, Node, Store, canonical_hash, envelope_id
from nodecules.core.strip_access import (
    Ago,
    AtPattern,
    AtTimePattern,
    FromSelf,
    Index,
    OrdinalPattern,
    FromHead,
    RangePattern,
    SelfWindowEnd,
    SelfWindowStart,
    at,
    last,
    mine,
)
from nodecules.core.strip_nodes import strip_append
from nodecules.core.time import TimeRange

S, LIB = "obs", "lib"


def _obs(t0: int, t1: int, what: str) -> dict:
    return {"ts": {"start_ms": t0, "end_ms": t1}, "what": what}


def _closed(elements, floor: int = 0) -> dict:
    return {"strip_floor": floor, "elements": list(elements), "strip_closed": True}


STRIP = [_obs(i * 1000, (i + 1) * 1000, f"e{i}") for i in range(3)]  # elements 0..2, times [0, 3000)


# --- declarations ------------------------------------------------------------


def test_a_declaration_written_before_promises_keeps_its_hash():
    edge = Edge(target="strips/obs", pattern=last(3))
    dumped = edge.model_dump(mode="json")
    assert "promise" not in dumped["pattern"] and "tolerance" not in dumped["pattern"]
    node = Node(id="n", kind="k", scope=S, edges=(edge,))
    by_hand = canonical_hash({"kind": "k", "data": None, "edges": [{"target": "strips/obs", "scope": None, "pattern": {"kind": "ordinal", "start": {"kind": "from_head", "offset": -3}, "end": {"kind": "from_head", "offset": 0}}, "role": "", "optional": False}]})
    assert node.content_hash() == by_hand
    nearest = Edge(target="strips/obs", pattern=at(0, promise="nearest", tolerance=2))
    assert Edge.model_validate(nearest.model_dump(mode="json")) == nearest  # the promise travels with the graph


# --- one element by position ---------------------------------------------------


def test_offset_zero_from_the_head_is_the_newest_element_and_from_self_is_mine():
    assert read(at(), STRIP, None, kind="strip").value["what"] == "e2"
    assert read(at(-2), STRIP, None, kind="strip").value["what"] == "e0"
    r = read(mine(), STRIP, None, position=1, kind="strip")
    assert r.value["what"] == "e1" and r.resolved["read"] == 1  # element i of another strip, for a per-element map


def test_an_exact_read_misses_with_a_reason_that_says_whether_to_wait():
    before = read(AtPattern(position=Index(index=0)), STRIP, None, kind="strip")
    assert before.value["what"] == "e0"
    gap = read(at(-5), STRIP, None, kind="strip")
    assert gap.missed and gap.reason == "gap" and gap.lost
    not_yet = read(mine(), STRIP, None, position=5, kind="strip")
    assert not_yet.reason == "not-yet" and not_yet.waiting and not not_yet.lost
    ended = read(mine(), _closed(STRIP), None, position=5, kind="strip")
    assert ended.reason == "ended" and ended.lost and "ended at element 2" in ended.lost
    released = read(AtPattern(position=Index(index=0)), {"strip_floor": 2, "elements": STRIP[2:]}, None, kind="strip")
    assert released.reason == "released"


def test_nearest_takes_the_closest_element_and_owes_the_exact_while_it_can_still_arrive():
    r = read(mine(promise="nearest"), STRIP, None, position=4, kind="strip")
    assert not r.missed and r.value["what"] == "e2" and r.distance == 2
    assert r.provisional and r.owed == "element 4" and r.resolved["owed"] == "element 4"
    final = read(mine(promise="nearest"), _closed(STRIP), None, position=4, kind="strip")
    assert final.value["what"] == "e2" and not final.provisional  # the strip ended: nothing closer will come
    too_far = read(mine(promise="nearest", tolerance=1), STRIP, None, position=4, kind="strip")
    assert too_far.reason == "not-yet"  # beyond tolerance: missed for the reason the exact is missing
    exact_is_zero_tolerance = read(mine(promise="nearest", tolerance=0), STRIP, None, position=4, kind="strip")
    assert exact_is_zero_tolerance.reason == "not-yet"


# --- one element by time ---------------------------------------------------------


def test_a_time_read_finds_the_element_containing_the_time_or_the_nearest():
    pattern = AtTimePattern(field="ts", time=Ago(anchor="head", ms=1500))
    assert read(pattern, STRIP, None, kind="strip").value["what"] == "e1"  # head 3000, t = 1500
    holey = [STRIP[0], STRIP[2]]  # nothing recorded in [1000, 2000)
    hole = AtTimePattern(field="ts", time=Ago(anchor="self_window_start"))
    w = TimeRange(start_ms=1500, end_ms=1600)
    assert read(hole, holey, w, kind="strip").reason == "gap"
    near = read(AtTimePattern(field="ts", time=SelfWindowStart(), promise="nearest"), holey, w, kind="strip")
    # spans are end-exclusive: 1500 is 500 ms before e2 starts and 501 ms past e0's last millisecond
    assert near.value["what"] == "e2" and near.distance == 500 and not near.provisional  # a past hole is final
    tie = read(AtTimePattern(field="ts", time=SelfWindowStart(), promise="nearest"), holey, TimeRange(start_ms=1499, end_ms=1600), kind="strip")
    assert tie.value["what"] == "e0" and tie.distance == 500  # equal distances: the earlier element
    future = read(AtTimePattern(field="ts", time=SelfWindowStart(), promise="nearest"), STRIP, TimeRange(start_ms=5000, end_ms=5100), kind="strip")
    assert future.value["what"] == "e2" and future.provisional  # past the head of an open strip


# --- spans -----------------------------------------------------------------------


def test_a_window_past_the_head_is_no_longer_silently_short():
    window = RangePattern(field="ts", start=SelfWindowStart(), end=SelfWindowEnd())
    w = TimeRange(start_ms=2000, end_ms=4000)  # the strip reaches 3000
    r = read(window, STRIP, w, kind="strip")
    assert r.reason == "not-yet" and "reaches 3000" in r.waiting
    nearest = RangePattern(field="ts", start=SelfWindowStart(), end=SelfWindowEnd(), promise="nearest")
    n = read(nearest, STRIP, w, kind="strip")
    assert [e["what"] for e in n.value] == ["e2"] and n.provisional and n.resolved["short_ms"] == 1000
    done = read(nearest, _closed(STRIP), w, kind="strip")
    assert [e["what"] for e in done.value] == ["e2"] and not done.provisional
    assert read(window, _closed(STRIP), w, kind="strip").reason == "ended"
    inside = read(window, STRIP, TimeRange(start_ms=0, end_ms=2000), kind="strip")
    assert [e["what"] for e in inside.value] == ["e0", "e1"]  # a window inside what exists is unchanged


def test_a_slice_past_the_end_of_a_closed_strip_is_ended_not_pending():
    tail = OrdinalPattern(start=FromSelf(offset=-1), end=FromSelf())
    assert read(tail, STRIP, None, position=5, kind="strip").reason == "not-yet"
    assert read(tail, _closed(STRIP), None, position=5, kind="strip").reason == "ended"
    clipped = read(OrdinalPattern(start=FromHead(offset=-10), end=FromHead()), STRIP, None, kind="strip")
    assert len(clipped.value) == 3 and clipped.resolved["clipped"] == "before the strip's first element"


# --- through the generator -------------------------------------------------------


def _declare(store: Store, pattern, params=None):
    async def echo(inputs, params):
        return {"seen": inputs.get("seen")}

    async def shout(inputs, params):
        return {"loud": str(inputs.get("summary"))}

    tx = store.transaction(LIB, author="dev")
    tx.put(Node(id="r/echo", kind=RECIPE_TEMPLATE_KIND, scope=LIB, data={"realization": "echo@1", "params": {}}))
    tx.put(Node(id="r/shout", kind=RECIPE_TEMPLATE_KIND, scope=LIB, data={"realization": "shout@1", "params": {}}))
    tx.commit()
    tx = store.transaction(S, author="dev")
    edges = [Edge(target="strips/obs", pattern=pattern, role="seen"), Edge(target="r/echo", scope=LIB, role=RECIPE_ROLE)]
    if params is not None:
        tx.put(Node(id="params/summary", kind="params", scope=S, data=params))
        edges.append(Edge(target="params/summary", role=PARAMS_ROLE))
    tx.put(Node(id="summary", kind="k", scope=S, edges=tuple(edges)))
    tx.put(Node(id="banner", kind="k", scope=S, edges=(Edge(target="summary", role="summary"), Edge(target="r/shout", scope=LIB, role=RECIPE_ROLE))))
    tx.commit()
    return [Realization("echo@1", echo, deterministic=True), Realization("shout@1", shout, deterministic=True)]


def test_wet_ink_a_provisional_result_and_everything_downstream_is_replaced_when_the_exact_lands():
    store = Store()
    strip_append(store, S, "strips/obs", *STRIP, author="camera")
    gen = Generator(store, _declare(store, mine(promise="nearest"), {"position": 3}), author="gen")

    first = asyncio.run(gen.produce(S, "banner"))
    summary = asyncio.run(gen.produce(S, "summary"))
    assert summary.provisional and summary.outcome == "via-substitute" and summary.node.data["seen"]["what"] == "e2"
    assert summary.envelope.data["recipe"]["owed"] == ["obs:strips/obs: element 3"]
    assert first.provisional and first.owed == ("obs:summary is provisional",)  # wet propagates
    wet_hash = summary.node.content_hash()

    strip_append(store, S, "strips/obs", _obs(3000, 4000, "e3"), author="camera")  # the exact lands
    dry = asyncio.run(gen.produce(S, "banner"))
    dry_summary = asyncio.run(gen.produce(S, "summary"))
    assert not dry_summary.provisional and dry_summary.outcome == "exact" and dry_summary.node.data["seen"]["what"] == "e3"
    assert not dry.provisional and dry.cooked
    # both versions stay in history: what was believed when stays answerable
    older = [m for m in [store.manifest(h) for h in store._manifests] if m.scope == S and m.hash_of("summary") == wet_hash]
    assert older


def test_a_pending_and_a_lost_production_carry_the_reason():
    store = Store()
    strip_append(store, S, "strips/obs", *STRIP, author="camera")
    gen = Generator(store, _declare(store, mine(), {"position": 7}), author="gen")
    g = asyncio.run(gen.produce(S, "summary"))
    assert g.outcome == "pending" and g.reason == "not-yet"
    store2 = Store()
    strip_append(store2, S, "strips/obs", *STRIP, author="camera")
    gen2 = Generator(store2, _declare(store2, at(-9)), author="gen")
    g2 = asyncio.run(gen2.produce(S, "summary"))
    assert g2.outcome == "lost" and g2.reason == "gap"


# --- acyclic at commit -------------------------------------------------------------


def test_a_store_that_refuses_cycles_refuses_one_at_commit_and_allows_a_reader_declared_before_its_source():
    store = Store(refuse_cycles=True)
    tx = store.transaction(S, author="dev")
    tx.put(Node(id="reader", kind="k", scope=S, edges=(Edge(target="strips/later"),)))  # declared first: allowed
    tx.commit()
    tx = store.transaction(S, author="dev")
    tx.put(Node(id="x", kind="k", scope=S, data=1, edges=(Edge(target="y"),)))
    tx.put(Node(id="y", kind="k", scope=S, data=1, edges=(Edge(target="x"),)))
    with pytest.raises(Cycle):
        tx.commit()
    tx = store.transaction(S, author="dev")
    tx.put(Node(id="self", kind="k", scope=S, edges=(Edge(target="self"),)))
    with pytest.raises(Cycle):
        tx.commit()
    tx = store.transaction(S, author="dev")
    tx.put(Node(id="a", kind="k", scope=S, data=1))
    tx.put(Node(id="b", kind="k", scope=S, data=1, edges=(Edge(target="a"),)))
    tx.put(Node(id="c", kind="k", scope=S, data=1, edges=(Edge(target="a"), Edge(target="b"))))
    tx.commit()  # a diamond is not a cycle
    assert Store().refuse_cycles is False  # off by default until the founder rules (two tests commit cycles on purpose)
