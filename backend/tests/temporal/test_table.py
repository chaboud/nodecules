"""The table: open collaboration at the edge, attention, following."""

from __future__ import annotations

import pytest

from nodecules.core.replica import CONFLICT_KIND, sync, transfer
from nodecules.core.scene import Element
from nodecules.core.store import Node, Store
from nodecules.core.table import (
    Focus,
    Participant,
    attend,
    follow,
    join,
    open_collaboration,
    participants,
    present,
    renew,
    view_for,
)

T = "table/room-1"
MS = 10_000  # table ticks per ms (100 ns ticks)
TTL = 5_000 * MS


def _card(name: str, region: str, text: str, t: int = 0, **payload) -> Node:
    return Node(id=name, kind="ui.card", scope=T, data=Element(present_at=t, expires_at=t + 3_600_000 * MS, region=region, payload={"text": text, **payload}).model_dump())


def _table() -> Store:
    open_collaboration()
    s = Store()
    s.set_resolution(T, "merge")
    join(s, T, Participant(id="alice", display="Alice"))
    join(s, T, Participant(id="bob", display="Bob"))
    join(s, T, Participant(id="butler", kind="agent", display="Butler"))
    tx = s.transaction(T, author="alice")
    tx.put(_card("card/plan", "main", "Plan the trip"))
    tx.commit()
    return s


def _replica(s: Store) -> Store:
    r = Store()
    transfer(s, r, T)
    r.set_head(T, s.current(T))
    return r


def test_participants_and_presence_with_expiry():
    s = _table()
    assert [p.id for p in participants(s, s.current(T))] == ["alice", "bob", "butler"]
    attend(s, T, "alice", Focus(region="main", element="card/plan"), now=0, ttl=TTL)
    attend(s, T, "bob", Focus(region="side"), now=1_000 * MS, ttl=TTL)
    m = s.current(T)
    assert present(s, m, 2_000 * MS) == ("alice", "bob")
    assert present(s, m, 5_500 * MS) == ("bob",)  # alice went quiet
    renew(s, T, "alice", until=9_000 * MS)
    assert present(s, s.current(T), 5_500 * MS) == ("alice", "bob")
    # attention shows up in the frame, in the presence region, so a presenter can draw cursors
    v = view_for(s, s.current(T), "bob", 2_000 * MS, timeline="table")
    assert [e.id for e in v.frame.elements if e.region == "presence"] == ["attention/alice", "attention/bob"]
    assert v.focus == Focus(region="side") and v.via is None and v.following is None


def test_following_is_optional_transitive_and_cycle_safe():
    s = _table()
    attend(s, T, "alice", Focus(region="main", element="card/plan"), now=0, ttl=TTL)
    attend(s, T, "bob", Focus(region="side"), now=0, ttl=TTL)
    attend(s, T, "butler", None, now=0, ttl=TTL)
    follow(s, T, "bob", "alice", now=0, ttl=TTL)
    v = view_for(s, s.current(T), "bob", 1_000 * MS, timeline="table")
    assert v.focus == Focus(region="main", element="card/plan") and v.via == "alice" and v.following == "alice"
    # alice's own view is her own; following bob does not move her
    assert view_for(s, s.current(T), "alice", 1_000 * MS, timeline="table").via is None
    # transitive: butler follows bob who follows alice
    follow(s, T, "butler", "bob", now=0, ttl=TTL)
    assert view_for(s, s.current(T), "butler", 1_000 * MS, timeline="table").via == "alice"
    # a cycle stops at the last resolvable focus, never loops
    follow(s, T, "alice", "butler", now=0, ttl=TTL)
    v = view_for(s, s.current(T), "alice", 1_000 * MS, timeline="table")
    assert v.via in ("bob", "butler") and v.focus is not None
    # when the followed participant goes quiet, I fall back to my own focus and via says so
    renew(s, T, "bob", until=9_000 * MS)  # bob is still here; alice's presence lapsed at 5 s
    v = view_for(s, s.current(T), "bob", 6_000 * MS, timeline="table")
    assert v.via is None and v.focus == Focus(region="side") and v.following == "alice"
    # unfollow
    follow(s, T, "bob", None, now=6_000 * MS, ttl=TTL)
    assert view_for(s, s.current(T), "bob", 7_000 * MS, timeline="table").following is None


def test_open_collaboration_converges_field_wise_across_replicas():
    a = _table()
    b = _replica(a)
    # alice moves the card and bob retitles it, concurrently, on their own replicas
    tx = a.transaction(T, author="alice")
    tx.put(_card("card/plan", "main", "Plan the trip", x=10, y=20))
    tx.commit()
    tx = b.transaction(T, author="bob")
    tx.put(_card("card/plan", "main", "Plan the summer trip"))
    tx.commit()
    ha, hb = sync(a, b, T)
    assert ha.content_hash() == hb.content_hash()
    got = a.get(ha, "card/plan")
    assert got.kind == "ui.card"  # not a conflict node
    assert got.data["payload"] == {"text": "Plan the summer trip", "x": 10, "y": 20}  # both edits survived
    # both change the same field: deterministic, identical on both sides, and the loser is in history
    tx = a.transaction(T, author="alice")
    tx.put(_card("card/plan", "main", "A", x=10, y=20))
    tx.commit()
    tx = b.transaction(T, author="bob")
    tx.put(_card("card/plan", "main", "B", x=10, y=20))
    tx.commit()
    ha, hb = sync(a, b, T)
    assert ha.content_hash() == hb.content_hash()
    assert a.get(ha, "card/plan").data["payload"]["text"] in ("A", "B")
    assert b.get(hb, "card/plan").data["payload"]["text"] == a.get(ha, "card/plan").data["payload"]["text"]
    assert not any(a.get(ha, n).kind == CONFLICT_KIND for n in ["card/plan"])


def test_attention_never_conflicts_and_survives_sync():
    a = _table()
    b = _replica(a)
    attend(a, T, "alice", Focus(region="main"), now=0, ttl=TTL)
    attend(b, T, "bob", Focus(region="side"), now=0, ttl=TTL)
    follow(b, T, "bob", "alice", now=0, ttl=TTL)
    sync(a, b, T)
    assert present(b, b.current(T), 1_000 * MS) == ("alice", "bob")
    assert view_for(b, b.current(T), "bob", 1_000 * MS, timeline="table").via == "alice"
