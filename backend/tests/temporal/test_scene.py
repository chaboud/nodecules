"""Tests for scenes — presentation time, expiry, and the frame as a pure
function; two simulated surfaces present the same element within the
computed bound, with no pixel anywhere."""

from __future__ import annotations

import pytest

from nodecules.core.scene import (
    Element,
    Surface,
    TablePolicy,
    degraded,
    expired,
    frame,
    lead,
    presented,
    renewed,
    schedule,
    simultaneity_bound,
    spread,
)
from nodecules.core.store import Node, Store
from nodecules.core.timeline import NANOSECONDS, Anchor, Timebase, Timeline, TimelineMap

TABLE = "table/room-1"
# the table's clock: 100 ns nanos, origin at table start
T_TABLE = Timeline(id="table", timebase=Timebase(num=1, den=10_000_000), origin="table-start", clock="derived")
T_WALL = Timeline(id="wall", timebase=NANOSECONDS, origin="device-boot", clock="monotonic", source="wall-display")
T_PHONE = Timeline(id="phone", timebase=NANOSECONDS, origin="device-boot", clock="monotonic", source="phone")
T_SLOW = Timeline(id="slow", timebase=NANOSECONDS, origin="device-boot", clock="monotonic", source="old-tablet")
TL = {t.id: t for t in (T_TABLE, T_WALL, T_PHONE, T_SLOW)}

MS = 10_000  # table ticks per millisecond

# wall: offset 3 s, no drift, ±0.5 ms sync error, 60 Hz
WALL = Surface(
    id="wall", timeline="wall", refresh=Timebase(num=1, den=60),
    to_local=TimelineMap(src="table", dst="wall", anchors=(Anchor(src=0, dst=3_000_000_000),), provenance="measured", error_ticks=500_000),
    delivery_ticks=2 * MS, render_ticks=1 * MS, modalities=("visual",),
)
# phone: offset 7 s, 200 ppm drift measured over ten minutes, ±2 ms error, 120 Hz
TEN_MIN_TABLE = 600 * 10_000_000
TEN_MIN_NS = 600 * 10**9
PHONE = Surface(
    id="phone", timeline="phone", refresh=Timebase(num=1, den=120),
    to_local=TimelineMap(src="table", dst="phone", anchors=(Anchor(src=0, dst=7 * 10**9), Anchor(src=TEN_MIN_TABLE, dst=7 * 10**9 + TEN_MIN_NS + 120 * 10**6)), provenance="measured", error_ticks=2_000_000),
    delivery_ticks=8 * MS, render_ticks=2 * MS, modalities=("visual", "haptic"),
)
# an old tablet: ±80 ms sync error, 10 Hz — cannot promise simultaneity
SLOW = Surface(
    id="slow", timeline="slow", refresh=Timebase(num=1, den=10),
    to_local=TimelineMap(src="table", dst="slow", anchors=(Anchor(src=0, dst=0),), provenance="assumed", error_ticks=80_000_000),
    delivery_ticks=50 * MS, render_ticks=20 * MS,
)
POLICY = TablePolicy(timeline="table", frame_clock=Timebase(num=1, den=60), tolerance_ticks=30 * MS, lateness="skip")


def _table(store: Store, *elements: tuple) -> None:
    tx = store.transaction(TABLE, author="kid")
    for name, el in elements:
        tx.put(Node(id=name, kind="ui.card", scope=TABLE, data=el.model_dump()))
    tx.commit()


def test_element_validity_is_a_span_and_persistence_is_renewal():
    el = Element(present_at=100 * MS, expires_at=500 * MS, payload={"text": "hi"})
    assert el.validity("table").contains(100 * MS) and not el.validity("table").contains(500 * MS)
    with pytest.raises(ValueError):
        Element(present_at=5, expires_at=5)
    node = Node(id="card/1", kind="ui.card", scope=TABLE, data=el.model_dump())
    longer = renewed(node, 900 * MS)
    assert longer.data["expires_at"] == 900 * MS and longer.content_hash() != node.content_hash()
    with pytest.raises(ValueError):
        renewed(node, 400 * MS)


def test_frame_is_a_pure_function_of_manifest_and_instant():
    store = Store()
    tx = store.transaction(TABLE, author="kid")
    tx.put(Node(id="card/b", kind="ui.card", scope=TABLE, data=Element(present_at=100 * MS, expires_at=400 * MS, region="main", z=1, payload="B").model_dump()))
    tx.put(Node(id="card/a", kind="ui.card", scope=TABLE, data=Element(present_at=100 * MS, expires_at=400 * MS, region="main", z=0, payload="A").model_dump()))
    tx.put(Node(id="later", kind="ui.card", scope=TABLE, data=Element(present_at=300 * MS, expires_at=600 * MS, region="side", payload="L").model_dump()))
    tx.put(Node(id="recipes/x", kind="recipe.template", scope=TABLE, data={"realization": "r"}))  # not element-shaped
    m = tx.commit()
    f1 = frame(store, m, 200 * MS, timeline="table")
    assert [e.id for e in f1.elements] == ["card/a", "card/b"]  # ordered by z, region, id — not by insertion
    assert f1.content_hash() == frame(store, m, 200 * MS, timeline="table").content_hash()
    f2 = frame(store, m, 350 * MS, timeline="table")
    assert [e.id for e in f2.elements] == ["card/a", "later", "card/b"]  # z first: both z=0 before card/b at z=1
    assert f2.content_hash() != f1.content_hash()
    assert frame(store, m, 50 * MS, timeline="table").elements == ()
    node = f1.as_node()
    assert node.kind == "frame" and node.data["manifest"] == m.content_hash() and node.data["t"] == 200 * MS


def test_expiry_removes_the_vanished_and_pruned_data_is_not_in_the_frame():
    store = Store()
    tx = store.transaction(TABLE, author="kid")
    tx.put(Node(id="alive", kind="ui.card", scope=TABLE, data=Element(present_at=0, expires_at=1000 * MS).model_dump()))
    tx.put(Node(id="gone", kind="ui.card", scope=TABLE, data=Element(present_at=0, expires_at=200 * MS).model_dump()))
    m = tx.commit()
    assert [e.id for e in frame(store, m, 100 * MS, timeline="table").elements] == ["alive", "gone"]
    assert [e.id for e in frame(store, m, 300 * MS, timeline="table").elements] == ["alive"]
    assert expired(store, m, 300 * MS) == ("gone",)
    store.prune(m.hash_of("alive"))
    assert frame(store, m, 300 * MS, timeline="table").elements == ()


def test_simultaneity_bound_is_knowable_per_surface_and_names_the_degraded():
    wall = simultaneity_bound(WALL, TL)
    phone = simultaneity_bound(PHONE, TL)
    slow = simultaneity_bound(SLOW, TL)
    # wall: 0.5 ms error + 16.67 ms refresh (ceil to ticks) + 2 + 1
    assert wall == 5_000 + 166_667 + 20_000 + 10_000
    # phone: 2 ms + 8.33 ms + 8 + 2
    assert phone == 20_000 + 83_334 + 80_000 + 20_000
    assert slow > POLICY.tolerance_ticks and wall < POLICY.tolerance_ticks and phone < POLICY.tolerance_ticks
    assert degraded((WALL, PHONE, SLOW), POLICY, TL) == ("slow",)
    assert lead((WALL, PHONE), TL) == max(wall, phone) == phone  # the phone's delivery latency makes it the worst


def test_two_surfaces_present_the_same_element_within_the_bound():
    store = Store()
    t_show = 5_000 * MS  # five seconds in
    tx = store.transaction(TABLE, author="kid")
    tx.put(Node(id="card/1", kind="ui.card", scope=TABLE, data=Element(present_at=t_show, expires_at=t_show + 2_000 * MS, payload="go").model_dump()))
    m = tx.commit()
    f = frame(store, m, t_show, timeline="table")
    # each surface receives the head well ahead (a second before, on its own clock)
    now_wall = 3_000_000_000 + 4 * 10**9
    now_phone = 7 * 10**9 + 4 * 10**9
    s_wall = schedule(WALL, f, now_wall, POLICY, TL)
    s_phone = schedule(PHONE, f, now_phone, POLICY, TL)
    assert all(i.status == "on-time" for i in (*s_wall.items, *s_phone.items))
    reports = {"wall": presented(WALL, s_wall, TL), "phone": presented(PHONE, s_phone, TL)}
    gap = spread(reports)["card/1"]
    assert gap <= max(simultaneity_bound(WALL, TL), simultaneity_bound(PHONE, TL))
    # and each surface's own error against the intended instant is within its own bound
    for sid, surf in (("wall", WALL), ("phone", PHONE)):
        assert abs(reports[sid]["card/1"] - t_show) <= simultaneity_bound(surf, TL)


def test_lateness_is_a_policy_skip_present_late_or_slip():
    store = Store()
    t_show = 1_000 * MS
    tx = store.transaction(TABLE, author="kid")
    tx.put(Node(id="card/1", kind="ui.card", scope=TABLE, data=Element(present_at=t_show, expires_at=t_show + 1_000 * MS).model_dump()))
    m = tx.commit()
    f = frame(store, m, t_show, timeline="table")
    late_now = 3_000_000_000 + 1_050_000_000  # the wall got the head 50 ms after the instant
    skip = schedule(WALL, f, late_now, POLICY, TL)
    assert skip.items[0].status == "skipped" and skip.items[0].local_tick is None
    late = schedule(WALL, f, late_now, POLICY.model_copy(update={"lateness": "present-late"}), TL)
    assert late.items[0].status == "late" and late.items[0].local_tick >= late_now
    r = (late.items[0].local_tick * 60) % 10**9
    assert min(r, 10**9 - r) < 60  # on the wall's own 1/60 s grid, to within one nanosecond tick
    slip = schedule(WALL, f, late_now, POLICY.model_copy(update={"lateness": "slip", "frame_clock": Timebase(num=1, den=10)}), TL)
    assert slip.items[0].status == "slipped" and slip.items[0].local_tick % (10**9 // 10) == 0  # on the table's slot grid
    assert presented(WALL, skip, TL) == {}


def test_publishing_ahead_by_the_lead_keeps_every_surface_on_time():
    store = Store()
    L = lead((WALL, PHONE), TL)
    t_now_table = 10_000 * MS
    t_show = t_now_table + L  # exactly the lead ahead
    tx = store.transaction(TABLE, author="kid")
    tx.put(Node(id="card/1", kind="ui.card", scope=TABLE, data=Element(present_at=t_show, expires_at=t_show + 1_000 * MS).model_dump()))
    m = tx.commit()
    f = frame(store, m, t_show, timeline="table")
    # each surface receives the head `delivery` after now, on its own clock
    from nodecules.core.timeline import Instant, convert

    for surf in (WALL, PHONE):
        arrive = convert(Instant(ticks=t_now_table + surf.delivery_ticks, timeline="table"), surf.to_local, TL).instant.ticks
        s = schedule(surf, f, arrive, POLICY, TL)
        assert s.items[0].status == "on-time", (surf.id, s.items[0])
