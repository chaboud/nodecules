"""Sources that come and go (vault ADR-0039): the founder's dock camera and a
Nest camera with transient power. One segment strip per connection, a
produced long strip over them with coverage, and reads that tell "saw
nothing" from "was not looking" and "the last of what was watched" from
"now"."""

from __future__ import annotations

import asyncio

from nodecules.core.generation import Generator, read
from nodecules.core.segments import append, backfill, current_segment, disconnect, merge_realization, open_segment, retire, segment_name, watch
from nodecules.core.store import Node, Store
from nodecules.core.strip_access import Ago, AtTimePattern, RangePattern, SelfWindowEnd, SelfWindowStart
from nodecules.core.time import TimeRange
from nodecules.core.transforms import receipt, verify

S, CAM = "desk", "sources/dock-cam"


def _seen(t0: int, t1: int, what: str) -> dict:
    return {"ts": {"start_ms": t0, "end_ms": t1}, "what": what}


def _long(store: Store) -> dict:
    g = asyncio.run(Generator(store, [merge_realization()], author="merge").produce(S, CAM))
    assert g.node is not None, (g.outcome, g.note, g.error)
    return g.node.data


LAST_3S = RangePattern(field="ts", start=Ago(anchor="head", ms=3000), end=Ago(anchor="head"))
LAST_3S_NEAREST = RangePattern(field="ts", start=Ago(anchor="head", ms=3000), end=Ago(anchor="head"), promise="nearest")


def test_the_dock_while_connected_the_last_three_seconds_are_now():
    store = Store()
    open_segment(store, S, CAM, at_ms=0, author="dock")
    append(store, S, CAM, *[_seen(i * 1000, (i + 1) * 1000, "cup") for i in range(5)], author="cam")
    long = _long(store)
    assert long["coverage"] == [[0, 5000]] and "away_since_ms" not in long
    r = read(LAST_3S, long, None, kind="strip")
    assert not r.missed and len(r.value) == 3 and not r.provisional


def test_the_dock_unplugged_the_last_of_what_was_watched_is_not_passed_off_as_now():
    store = Store()
    open_segment(store, S, CAM, at_ms=0, author="dock")
    append(store, S, CAM, *[_seen(i * 1000, (i + 1) * 1000, "cup") for i in range(5)], author="cam")
    m = disconnect(store, S, CAM, at_ms=5000, author="dock")  # the operating system reported the unplug: a fact
    assert current_segment(store, S, CAM) is None and receipt(store, store.current(S), segment_name(CAM, 1))["transform"] == "segment.close@1"
    long = _long(store)
    assert long["away_since_ms"] == 5000
    exact = read(LAST_3S, long, None, kind="strip")
    assert exact.reason == "not-yet" and "away since 5000" in exact.waiting
    near = read(LAST_3S_NEAREST, long, None, kind="strip")
    assert len(near.value) == 3 and near.provisional and "away since 5000" in near.owed


def test_the_dock_reconnected_the_dead_band_is_a_gap_and_reads_say_so():
    store = Store()
    open_segment(store, S, CAM, at_ms=0, author="dock")
    append(store, S, CAM, *[_seen(i * 1000, (i + 1) * 1000, "cup") for i in range(5)], author="cam")
    disconnect(store, S, CAM, at_ms=5000, author="dock")
    # ten minutes later; the camera's clock restarted at zero, so its map puts local 0 at source 600000
    open_segment(store, S, CAM, at_ms=0, offset_ms=600_000, author="dock")
    append(store, S, CAM, _seen(0, 1000, "dog"), author="cam")
    long = _long(store)
    assert long["coverage"] == [[0, 5000], [600_000, 601_000]] and "away_since_ms" not in long
    assert long["elements"][-1]["ts"] == {"start_ms": 600_000, "end_ms": 601_000} and long["elements"][-1]["segment"] == 2
    window = RangePattern(field="ts", start=SelfWindowStart(), end=SelfWindowEnd())
    gap = read(window, long, TimeRange(start_ms=4000, end_ms=601_000), kind="strip")
    assert gap.reason == "gap" and "[5000, 600000]" in gap.lost
    near = read(RangePattern(field="ts", start=SelfWindowStart(), end=SelfWindowEnd(), promise="nearest"), long, TimeRange(start_ms=4000, end_ms=601_000), kind="strip")
    assert [e["what"] for e in near.value] == ["cup", "dog"] and near.resolved["gaps"] == [[5000, 600_000]] and not near.provisional
    at = read(AtTimePattern(field="ts", time=SelfWindowStart()), long, TimeRange(start_ms=300_000, end_ms=300_001), kind="strip")
    assert at.reason == "gap"  # nobody was looking: never, a fact
    recent = read(LAST_3S, long, None, kind="strip")
    assert recent.reason == "gap"  # the last three seconds reach back into the dead band


def test_saw_nothing_is_not_the_same_as_was_not_looking():
    store = Store()
    open_segment(store, S, CAM, at_ms=0, author="dock")
    append(store, S, CAM, _seen(0, 1000, "cup"), author="cam")
    watch(store, S, CAM, to_ms=8000, author="cam")  # watching, nothing new: no placeholder element
    long = _long(store)
    assert long["coverage"] == [[0, 8000]] and len(long["elements"]) == 1
    quiet = read(AtTimePattern(field="ts", time=SelfWindowStart()), long, TimeRange(start_ms=5000, end_ms=5001), kind="strip")
    assert not quiet.missed and quiet.value is None and quiet.resolved["covered"]


def test_the_nest_loses_power_silently_and_its_return_closes_the_old_session():
    store = Store()
    open_segment(store, S, CAM, at_ms=0, author="hub")
    append(store, S, CAM, _seen(0, 1000, "porch"), author="nest")
    watch(store, S, CAM, to_ms=4000, author="nest")
    # power fails: nothing is written, and silence is not a fact, so the segment stays open
    assert current_segment(store, S, CAM) == segment_name(CAM, 1)
    open_segment(store, S, CAM, at_ms=0, offset_ms=900_000, author="hub")  # a new boot proves the old session over
    old = store.get(store.current(S), segment_name(CAM, 1)).data
    assert old["strip_closed"] and old["segment"]["closed_ms"] == 4000 and old["segment"]["closed_because"] == "a new session began"
    assert _long(store)["coverage"] == [[0, 4000], [900_000, 900_000]]


def test_backfill_fills_a_gap_without_touching_the_live_segment_and_every_write_verifies():
    store = Store()
    open_segment(store, S, CAM, at_ms=0, author="dock")
    append(store, S, CAM, _seen(0, 1000, "cup"), author="cam")
    disconnect(store, S, CAM, at_ms=1000, author="dock")
    open_segment(store, S, CAM, at_ms=0, offset_ms=10_000, author="dock")
    backfill(store, S, CAM, _seen(4000, 5000, "buffered"), from_ms=1000, to_ms=10_000, author="cam")
    assert current_segment(store, S, CAM) == segment_name(CAM, 2)
    long = _long(store)
    assert long["coverage"] == [[0, 10_000]] and [e["what"] for e in long["elements"]] == ["cup", "buffered"]
    m = store.current(S)
    assert all(verify(store, m, segment_name(CAM, n)) for n in (1, 2, 3))
    retire(store, S, CAM, author="dock")
    assert _long(store).get("strip_closed") is True
