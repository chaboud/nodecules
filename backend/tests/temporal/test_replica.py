"""Tests for replicas — the convergence properties of the CRDT basis."""

from __future__ import annotations

import pytest

from nodecules.core.replica import CONFLICT_KIND, common_ancestor, merge, sync, transfer
from nodecules.core.store import Node, Store, register_merge

T = "play/table-1"


def _seed() -> Store:
    s = Store()
    tx = s.transaction(T, author="seed")
    tx.put(Node(id="base", kind="k", scope=T, data=0))
    tx.put(Node(id="pile", kind="play.pile", scope=T, data={"bricks": ["red"]}))
    tx.put(Node(id="doomed", kind="k", scope=T, data="x"))
    tx.commit("genesis content")
    return s


def _replica_of(s: Store) -> Store:
    r = Store()
    transfer(s, r, T)
    r.set_head(T, s.current(T))
    return r


def _put(s: Store, name: str, data, kind: str = "k", author: str = "kid") -> None:
    tx = s.transaction(T, author=author)
    tx.put(Node(id=name, kind=kind, scope=T, data=data))
    tx.commit()


def test_a_replica_starts_from_the_same_head():
    a = _seed()
    b = _replica_of(a)
    assert b.current(T).content_hash() == a.current(T).content_hash()
    assert b.get(b.current(T), "pile").data == {"bricks": ["red"]}


def test_fast_forward_when_one_side_only_advanced():
    a = _seed()
    b = _replica_of(a)
    _put(a, "new", 1)
    ha, hb = sync(a, b, T)
    assert ha.content_hash() == hb.content_hash() == a.current(T).content_hash()
    assert hb.merge_parent is None  # no merge was needed
    assert isinstance(b.get(hb, "new"), Node)


def test_disjoint_offline_edits_converge_to_one_head_in_either_order():
    a = _seed()
    b = _replica_of(a)
    _put(a, "from-a", "A", author="alice")
    _put(b, "from-b", "B", author="bob")
    ha, hb = sync(a, b, T)
    assert ha.content_hash() == hb.content_hash()
    assert ha.merge_parent is not None and ha.parent < ha.merge_parent  # canonical parent order
    for s in (a, b):
        head = s.current(T)
        assert isinstance(s.get(head, "from-a"), Node) and isinstance(s.get(head, "from-b"), Node)
    # the same two histories synced the other way round produce the very same merge manifest
    a2 = _seed()
    b2 = _replica_of(a2)
    _put(a2, "from-a", "A", author="alice")
    _put(b2, "from-b", "B", author="bob")
    hb2, ha2 = sync(b2, a2, T)
    assert ha2.content_hash() == ha.content_hash()


def test_contested_name_with_a_rule_converges_even_if_the_rule_is_not_commutative():
    def concat(base, lo, hi):  # deliberately order-dependent
        return Node(id=lo.id, kind=lo.kind, scope=lo.scope, data={"bricks": lo.data["bricks"] + hi.data["bricks"]})

    register_merge("play.pile", concat)
    a = _seed()
    b = _replica_of(a)
    _put(a, "pile", {"bricks": ["red", "blue"]}, kind="play.pile", author="alice")
    _put(b, "pile", {"bricks": ["red", "green"]}, kind="play.pile", author="bob")
    ha, hb = sync(a, b, T)
    assert ha.content_hash() == hb.content_hash()
    merged = a.get(ha, "pile")
    assert merged.kind == "play.pile" and set(merged.data["bricks"]) == {"red", "blue", "green"}
    # order-independent: the other sync order yields the same merged body
    a2 = _seed()
    b2 = _replica_of(a2)
    _put(a2, "pile", {"bricks": ["red", "blue"]}, kind="play.pile", author="alice")
    _put(b2, "pile", {"bricks": ["red", "green"]}, kind="play.pile", author="bob")
    hb2, _ = sync(b2, a2, T)
    assert hb2.hash_of("pile") == ha.hash_of("pile")


def test_contested_name_without_a_rule_becomes_the_same_conflict_node_on_both_sides():
    a = _seed()
    b = _replica_of(a)
    _put(a, "base", 1, author="alice")
    _put(b, "base", 2, author="bob")
    va = a.current(T).hash_of("base")
    vb = b.current(T).hash_of("base")
    ha, hb = sync(a, b, T)
    assert ha.content_hash() == hb.content_hash()
    c = a.get(ha, "base")
    assert c.kind == CONFLICT_KIND and c.data["versions"] == sorted([va, vb])
    assert c.data["base"] == _seed().current(T).hash_of("base")
    assert a.has_body(va) and a.has_body(vb) and b.has_body(va) and b.has_body(vb)  # nothing lost
    # a later commit resolves it, and the resolution syncs as a fast-forward
    _put(a, "base", 2, author="alice")  # alice takes bob's value
    ha2, hb2 = sync(a, b, T)
    assert ha2.content_hash() == hb2.content_hash() and b.get(hb2, "base").data == 2


def test_delete_versus_change_keeps_the_change_and_delete_versus_untouched_deletes():
    a = _seed()
    b = _replica_of(a)
    tx = a.transaction(T, author="alice")
    tx.delete("base")
    tx.delete("doomed")
    tx.commit()
    _put(b, "base", 9, author="bob")
    ha, hb = sync(a, b, T)
    assert ha.content_hash() == hb.content_hash()
    assert a.get(ha, "base").data == 9  # bob's change survived alice's delete
    assert a.get(ha, "doomed") is None  # nobody touched it but alice; it is gone


def test_three_replicas_converge():
    a = _seed()
    b = _replica_of(a)
    c = _replica_of(a)
    _put(a, "a", 1)
    _put(b, "b", 2)
    _put(c, "c", 3)
    sync(a, b, T)
    sync(b, c, T)
    sync(a, c, T)
    sync(a, b, T)
    heads = {s.current(T).content_hash() for s in (a, b, c)}
    assert len(heads) == 1
    head = a.current(T)
    assert all(isinstance(a.get(head, n), Node) for n in ("a", "b", "c"))


def test_common_ancestor_on_a_diamond_and_merge_manifest_shape():
    a = _seed()
    base = a.current(T)
    b = _replica_of(a)
    _put(a, "x", 1)
    _put(b, "y", 1)
    transfer(b, a, T)
    lca = common_ancestor(a, a.current(T), b.current(T))
    assert lca.content_hash() == base.content_hash()
    m = merge(a, a.current(T), b.current(T))
    assert sorted(m.parents) == sorted([a.current(T).content_hash(), b.current(T).content_hash()])
    assert m.seq == max(a.current(T).seq, b.current(T).seq) + 1 and m.note == "merge"
    assert a.manifest(m.content_hash()) is m and a.current(T) is not m  # registered, head not moved


def test_transfer_moves_only_what_is_missing():
    a = _seed()
    b = _replica_of(a)
    assert transfer(a, b, T) == (0, 0)
    _put(a, "new", {"big": "payload"})
    assert transfer(a, b, T) == (1, 1)
    assert transfer(a, b, T) == (0, 0)
