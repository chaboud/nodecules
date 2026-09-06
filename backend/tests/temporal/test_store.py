"""Tests for PR-r3a — the node store's declarative core."""

from __future__ import annotations

import pytest

from nodecules.core.store import (
    ENVELOPE_KIND,
    register_merge,
    MANIFEST_KIND,
    Absent,
    Conflict,
    Cycle,
    DanglingEdge,
    Edge,
    Lost,
    Node,
    Store,
    envelope_id,
    make_envelope,
)
from nodecules.core.strip_access import LatestPattern

M = "project/stenota/meeting/abc123"
LIB = "project/stenota/library/recipes"


def _audio(scope: str = M, path: str = "meeting.wav") -> Node:
    return Node(id="audio.wav", kind="raw.audio", scope=scope, data={"path": path})


def _asr(scope: str = M, n: int = 3) -> Node:
    return Node(
        id="strips/asr/segments",
        kind="asr.segment",
        scope=scope,
        data={"segments": list(range(n))},
        edges=(Edge(target="audio.wav", pattern=LatestPattern(), role="audio"),),
    )


def _cooked(store: Store, scope: str = M):
    tx = store.transaction(scope)
    tx.put(_audio(scope))
    tx.put(_asr(scope))
    return tx.commit("first cook")


# --- identity ----------------------------------------------------------------


def test_content_hash_excludes_id_and_scope_and_covers_kind_data_edges():
    a = _asr()
    renamed = a.model_copy(update={"id": "asr/other"})
    rescoped = a.model_copy(update={"scope": "elsewhere"})
    assert a.content_hash() == renamed.content_hash() == rescoped.content_hash()
    assert a.model_copy(update={"kind": "asr.words"}).content_hash() != a.content_hash()
    assert _asr(n=4).content_hash() != a.content_hash()
    rewired = a.model_copy(update={"edges": (Edge(target="other.wav", role="audio"),)})
    assert rewired.content_hash() != a.content_hash()


def test_described_but_unproduced_is_a_body_not_a_residency_state():
    declared = Node(id="strips/asr/segments", kind="asr.segment", scope=M, data=None)
    assert declared.content_hash() != _asr().content_hash()
    store = Store()
    tx = store.transaction(M)
    tx.put(declared)
    m = tx.commit()
    got = store.get(m, "strips/asr/segments")
    assert isinstance(got, Node) and got.data is None


# --- manifests and snapshots ---------------------------------------------------


def test_a_scope_springs_into_being_with_a_genesis_manifest():
    store = Store()
    g = store.current(M)
    assert g.seq == 0 and g.parent is None and len(g) == 0
    assert store.current(M) is g
    assert store.scopes() == (M,)


def test_commit_advances_seq_and_chains_parents():
    store = Store()
    m1 = _cooked(store)
    tx = store.transaction(M)
    tx.put(_asr(n=5))
    m2 = tx.commit("recook")
    assert (m1.seq, m2.seq) == (1, 2)
    assert m2.parent == m1.content_hash()
    assert [m.seq for m in store.history(M)] == [2, 1, 0]
    assert store.manifest(m1.content_hash()) is m1


def test_manifest_is_a_node_and_is_stored_as_one():
    store = Store()
    m1 = _cooked(store)
    as_node = m1.as_node()
    assert as_node.kind == MANIFEST_KIND and as_node.scope == M
    assert as_node.data["root"] == m1.root and as_node.data["seq"] == 1
    assert store.get_by_hash(m1.content_hash()) == as_node


def test_reads_through_an_old_manifest_are_unaffected_by_later_commits():
    store = Store()
    m1 = _cooked(store)
    snap = store.snapshot(M)
    tx = store.transaction(M)
    tx.put(_asr(n=9))
    tx.delete("audio.wav")
    m2 = tx.commit()
    old = store.get(m1, "strips/asr/segments")
    new = store.get(m2, "strips/asr/segments")
    assert isinstance(old, Node) and old.data == {"segments": [0, 1, 2]}
    assert isinstance(new, Node) and new.data["segments"] == list(range(9))
    assert isinstance(store.get(m1, "audio.wav"), Node)
    assert store.get(m2, "audio.wav") is None
    assert snap.manifest(M) is m1 and snap.pinned(M)


def test_unbound_name_reads_as_none():
    store = Store()
    m1 = _cooked(store)
    assert store.get(m1, "never/existed") is None


def test_same_content_under_two_names_is_one_body():
    store = Store()
    tx = store.transaction(M)
    h1 = tx.put(_audio())
    h2 = tx.put(_audio().model_copy(update={"id": "audio-copy.wav"}))
    m = tx.commit()
    assert h1 == h2
    assert m.hash_of("audio.wav") == m.hash_of("audio-copy.wav") == h1
    assert len(store._blobs) == 2  # the shared body and the manifest node


def test_manifest_root_is_content_only_while_content_hash_is_a_version():
    """Two scopes with the same name→hash set agree on `root` without
    coordination (P-26); their manifest nodes still differ, because a
    manifest's identity includes its lineage."""
    store = Store()
    m_a = _cooked(store, "replica/a")
    m_b = _cooked(store, "replica/b")
    assert m_a.root == m_b.root
    assert m_a.content_hash() != m_b.content_hash()
    assert dict(m_a.entries()) == dict(m_b.entries())


def test_diff_between_manifests():
    store = Store()
    m1 = _cooked(store)
    tx = store.transaction(M)
    tx.put(_asr(n=9))
    tx.put(Node(id="strips/diar/segments", kind="diar.segment", scope=M, data={"turns": []}))
    tx.delete("audio.wav")
    m2 = tx.commit()
    d = store.diff(m1, m2)
    assert set(d.added) == {"strips/diar/segments"}
    assert set(d.removed) == {"audio.wav"}
    assert set(d.changed) == {"strips/asr/segments"}
    with pytest.raises(ValueError):
        store.diff(m1, store.current(LIB))


# --- transactions: CAS and rebase ---------------------------------------------


def test_disjoint_concurrent_transactions_both_commit():
    store = Store()
    _cooked(store)
    t1 = store.transaction(M)
    t2 = store.transaction(M)
    t1.put(Node(id="strips/diar/segments", kind="diar.segment", scope=M, data={"turns": [1]}))
    t2.put(Node(id="strips/vad/segments", kind="vad.segment", scope=M, data={"on": [0]}))
    m2 = t1.commit()
    m3 = t2.commit()  # base moved under it; disjoint names → rebased onto m2
    assert (m2.seq, m3.seq) == (2, 3) and m3.parent == m2.content_hash()
    assert "strips/diar/segments" in m3 and "strips/vad/segments" in m3
    assert store.current(M) is m3


def test_overlapping_concurrent_transactions_conflict_by_name():
    store = Store()
    _cooked(store)
    t1 = store.transaction(M)
    t2 = store.transaction(M)
    t1.put(_asr(n=5))
    t2.put(_asr(n=6))
    t2.put(Node(id="strips/vad/segments", kind="vad.segment", scope=M, data={}))
    t1.commit()
    with pytest.raises(Conflict) as ei:
        t2.commit()
    assert ei.value.keys == {"strips/asr/segments"}
    assert store.current(M).seq == 2  # the losing transaction changed nothing
    assert "strips/vad/segments" not in store.current(M)


def test_delete_versus_put_on_the_same_name_conflicts():
    store = Store()
    _cooked(store)
    t1 = store.transaction(M)
    t2 = store.transaction(M)
    t1.delete("audio.wav")
    t2.put(_audio(path="other.wav"))
    t1.commit()
    with pytest.raises(Conflict):
        t2.commit()


def test_transaction_refuses_foreign_scope_and_double_commit():
    store = Store()
    tx = store.transaction(M)
    with pytest.raises(ValueError):
        tx.put(_audio(scope=LIB))
    tx.put(_audio())
    tx.commit()
    with pytest.raises(RuntimeError):
        tx.commit()
    with pytest.raises(RuntimeError):
        tx.put(_audio())


def test_put_after_delete_in_one_transaction_is_a_put():
    store = Store()
    _cooked(store)
    tx = store.transaction(M)
    tx.delete("audio.wav")
    tx.put(_audio(path="replacement.wav"))
    m = tx.commit()
    got = store.get(m, "audio.wav")
    assert isinstance(got, Node) and got.data == {"path": "replacement.wav"}


def test_conflict_names_both_versions():
    store = Store()
    _cooked(store)
    t1, t2 = store.transaction(M), store.transaction(M)
    t1.put(_asr(n=5))
    t2.put(_asr(n=6))
    t1.commit()
    with pytest.raises(Conflict) as ei:
        t2.commit()
    ours, theirs = ei.value.versions["strips/asr/segments"]
    assert ours == _asr(n=6).content_hash() and theirs == _asr(n=5).content_hash()
    assert ei.value.policy == "single-authority"


# --- resolution policy: several writers on one scope ---------------------------------


def test_resolution_is_a_labelled_versioned_property_on_the_manifest():
    store = Store()
    g = store.current(M)
    assert g.resolution == "single-authority" and g.resolution_version == 1
    assert g.as_node().data["resolution"] == "single-authority"
    m = store.set_resolution(M, "last-writer-wins")
    assert (m.seq, m.resolution, m.resolution_version) == (1, "last-writer-wins", 2)
    assert m.parent == g.content_hash() and m.root == g.root  # same content, new policy
    assert store.set_resolution(M, "last-writer-wins") is m  # idempotent
    with pytest.raises(ValueError):
        store.set_resolution(M, "consensus-by-vibes")  # type: ignore[arg-type]
    # a later ordinary commit carries the policy forward unchanged
    tx = store.transaction(M)
    tx.put(_audio())
    m2 = tx.commit()
    assert (m2.resolution, m2.resolution_version) == ("last-writer-wins", 2)


def test_last_writer_wins_lets_the_commit_through_and_records_what_it_overrode():
    store = Store()
    _cooked(store)
    store.set_resolution(M, "last-writer-wins")
    t1, t2 = store.transaction(M), store.transaction(M)
    t1.put(_asr(n=5))
    t2.put(_asr(n=6))
    t1.commit()
    m = t2.commit()
    assert m.hash_of("strips/asr/segments") == _asr(n=6).content_hash()
    assert m.overrode == (("strips/asr/segments", _asr(n=5).content_hash()),)
    assert m.as_node().data["overrode"] == [["strips/asr/segments", _asr(n=5).content_hash()]]
    # the loser is still a resident body: nothing was destroyed, only unbound
    assert store.get_by_hash(_asr(n=5).content_hash()) is not None


def test_merge_folds_two_concurrent_versions_through_the_kinds_registered_rule():
    """Legos on a play table: two agents each add bricks to the same region
    at once; the region's kind knows that bricks union."""

    def union_bricks(base, ours, theirs):
        seen = set(base.data["bricks"]) if base else set()
        merged = list(base.data["bricks"]) if base else []
        for side in (ours, theirs):
            for b in side.data["bricks"]:
                if b not in seen:
                    seen.add(b)
                    merged.append(b)
        return Node(id=ours.id, kind=ours.kind, scope=ours.scope, data={"bricks": merged})

    register_merge("play.region", union_bricks)
    store = Store()
    tx = store.transaction(M)
    tx.put(Node(id="table/left", kind="play.region", scope=M, data={"bricks": ["red-2x4"]}))
    tx.commit()
    store.set_resolution(M, "merge")
    kid_a, kid_b = store.transaction(M), store.transaction(M)
    kid_a.put(Node(id="table/left", kind="play.region", scope=M, data={"bricks": ["red-2x4", "blue-1x2"]}))
    kid_b.put(Node(id="table/left", kind="play.region", scope=M, data={"bricks": ["red-2x4", "green-2x2"]}))
    kid_a.commit()
    m = kid_b.commit()
    got = store.get(m, "table/left")
    assert isinstance(got, Node)
    assert got.data["bricks"][0] == "red-2x4"  # the shared base first
    assert set(got.data["bricks"]) == {"red-2x4", "blue-1x2", "green-2x2"}  # nobody's bricks lost
    assert m.overrode == ()


def test_merge_without_a_rule_for_the_kind_conflicts_with_both_versions_named():
    store = Store()
    _cooked(store)
    store.set_resolution(M, "merge")
    t1, t2 = store.transaction(M), store.transaction(M)
    t1.put(_asr(n=5))
    t2.put(_asr(n=6))
    t1.commit()
    with pytest.raises(Conflict) as ei:
        t2.commit()
    assert ei.value.policy == "merge"
    assert ei.value.versions == {"strips/asr/segments": (_asr(n=6).content_hash(), _asr(n=5).content_hash())}


# --- generations: feedback and cross-system loops cross a boundary explicitly ------


def test_a_pinned_edge_reads_a_prior_generation_and_is_not_a_cycle():
    """A node reading its own previous output: the edge pins the generation
    it reads from, so the graph is still a DAG — per generation."""
    store = Store()
    tx = store.transaction(M)
    tx.put(Node(id="w/state", kind="settling", scope=M, data={"gen": 0, "v": 1.0}))
    m1 = tx.commit()
    tx = store.transaction(M)
    tx.put(
        Node(
            id="w/state",
            kind="settling",
            scope=M,
            data={"gen": 1, "v": 0.9},
            edges=(Edge(target="w/state", role="prev", at=m1.content_hash()),),
        )
    )
    m2 = tx.commit()
    snap = store.snapshot(M)
    h = store.composed_hash(snap, M, "w/state")
    prev = store.follow(snap, M, store.get(m2, "w/state").edges[0])
    assert isinstance(prev, Node) and prev.data["gen"] == 0
    # the pin is part of identity: the same body pinned to a different generation is a different node
    tx = store.transaction(M)
    tx.put(
        Node(
            id="w/state",
            kind="settling",
            scope=M,
            data={"gen": 1, "v": 0.9},
            edges=(Edge(target="w/state", role="prev", at=m2.content_hash()),),
        )
    )
    m3 = tx.commit()
    assert m3.hash_of("w/state") != m2.hash_of("w/state")
    assert store.composed_hash(store.snapshot(M), M, "w/state") != h


def test_cross_scope_loop_is_fine_across_generations_and_a_cycle_within_one():
    store = Store()
    tx = store.transaction(M)
    tx.put(Node(id="a", kind="k", scope=M, data=0))
    m_gen1 = tx.commit()
    tx = store.transaction(LIB)
    tx.put(Node(id="b", kind="k", scope=LIB, data=0, edges=(Edge(target="a", scope=M, at=m_gen1.content_hash()),)))
    tx.commit()
    tx = store.transaction(M)
    tx.put(Node(id="a", kind="k", scope=M, data=1, edges=(Edge(target="b", scope=LIB),)))
    tx.commit()
    assert store.composed_hash(store.snapshot(M, LIB), M, "a")  # a -> b -> a@gen1: not a cycle
    tx = store.transaction(LIB)
    tx.put(Node(id="b", kind="k", scope=LIB, data=1, edges=(Edge(target="a", scope=M),)))  # unpinned
    tx.commit()
    with pytest.raises(Cycle):
        store.composed_hash(store.snapshot(M, LIB), M, "a")


def test_a_pin_can_only_name_the_past():
    store = Store()
    tx = store.transaction(M)
    with pytest.raises(ValueError, match="only name the past"):
        tx.put(Node(id="x", kind="k", scope=M, data=0, edges=(Edge(target="x", at="deadbeef" * 8),)))
    other = store.current(LIB)
    with pytest.raises(ValueError, match="only name the past"):
        tx.put(Node(id="x", kind="k", scope=M, data=0, edges=(Edge(target="x", at=other.content_hash()),)))  # wrong scope


# --- residency: prune, envelopes, restore ---------------------------------------


def test_prune_without_an_envelope_reads_as_lost_with_identity_and_lineage_intact():
    store = Store()
    m1 = _cooked(store)
    h = _asr().content_hash()
    root_before = m1.root
    store.prune(h)
    got = store.get(m1, "strips/asr/segments")
    assert isinstance(got, Lost)
    assert got.content_hash == h and got.kind == "asr.segment"
    assert [e.target for e in got.edges] == ["audio.wav"]
    assert store.residency(h) == "pruned"
    assert store.get_by_hash(h) is None
    assert m1.root == root_before and store.current(M) is m1


def test_prune_with_an_envelope_reads_as_absent_naming_the_rebuild_slate():
    store = Store()
    asr = _asr()
    tx = store.transaction(M)
    tx.put(_audio())
    tx.put(asr)
    env = make_envelope(
        asr,
        recipe={"realization": "stenota.asr_faster_whisper@0.1.0"},
        inputs={"audio.wav": _audio().content_hash()},
        cooked_at_ms=120_000,
    )
    tx.put(env)
    m1 = tx.commit()
    store.prune(asr.content_hash())
    got = store.get(m1, "strips/asr/segments")
    assert isinstance(got, Absent)
    assert got.rebuild_via == envelope_id("strips/asr/segments") == env.id
    slate = store.get(m1, got.rebuild_via)
    assert isinstance(slate, Node) and slate.kind == ENVELOPE_KIND
    assert slate.data["of"] == "strips/asr/segments"
    assert slate.data["content_hash"] == asr.content_hash()
    assert slate.data["inputs"] == {"audio.wav": _audio().content_hash()}
    assert slate.edges == asr.edges  # lineage travels with the envelope


def test_envelope_and_content_are_distinct_nodes_with_distinct_identities():
    asr = _asr()
    env = make_envelope(asr, recipe={}, inputs={})
    assert env.id != asr.id and env.content_hash() != asr.content_hash()
    assert env.kind == ENVELOPE_KIND and env.scope == asr.scope


def test_restore_readmits_bytes_that_hash_to_the_manifests_identity():
    store = Store()
    m1 = _cooked(store)
    asr = _asr()
    store.prune(asr.content_hash())
    assert isinstance(store.get(m1, "strips/asr/segments"), Lost)
    # Whether these bytes came from a replica or a recompute is not the
    # store's concern (ADR-0018): they hash to what the manifest says.
    rebuilt = Node(
        id="anything",  # names are not identity
        kind="asr.segment",
        scope=M,
        data={"segments": [0, 1, 2]},
        edges=(Edge(target="audio.wav", pattern=LatestPattern(), role="audio"),),
    )
    assert store.restore(rebuilt) == asr.content_hash()
    assert store.residency(asr.content_hash()) == "ram"
    assert isinstance(store.get(m1, "strips/asr/segments"), Node)


def test_restore_refuses_bytes_nobody_asked_for():
    store = Store()
    _cooked(store)
    with pytest.raises(ValueError):
        store.restore(_asr(n=99))  # not a pruned identity
    with pytest.raises(ValueError):
        store.restore(_asr())  # resident already, not pruned
    with pytest.raises(KeyError):
        store.prune("not-a-hash")


# --- composed hash ------------------------------------------------------------


def test_composed_hash_changes_with_upstream_and_not_with_siblings():
    store = Store()
    _cooked(store)
    h1 = store.composed_hash(store.snapshot(M), M, "strips/asr/segments")
    tx = store.transaction(M)
    tx.put(Node(id="strips/diar/segments", kind="diar.segment", scope=M, data={"t": 1}))
    tx.commit()
    assert store.composed_hash(store.snapshot(M), M, "strips/asr/segments") == h1
    tx = store.transaction(M)
    tx.put(_audio(path="different.wav"))
    tx.commit()
    h2 = store.composed_hash(store.snapshot(M), M, "strips/asr/segments")
    assert h2 != h1
    assert h2 != _asr().content_hash()  # composed, not own


def test_composed_hash_survives_pruning():
    """Identity is answerable after compaction — the P-24 requirement."""
    store = Store()
    _cooked(store)
    snap = store.snapshot(M)
    before = store.composed_hash(snap, M, "strips/asr/segments")
    store.prune(_asr().content_hash())
    store.prune(_audio().content_hash())
    assert store.composed_hash(snap, M, "strips/asr/segments") == before


def test_composed_hash_crosses_scopes_by_snapshot_or_latest():
    store = Store()
    tx = store.transaction(LIB)
    tx.put(Node(id="recipes/asr", kind="recipe.template", scope=LIB, data={"v": 1}))
    tx.commit()
    tx = store.transaction(M)
    tx.put(_audio())
    tx.put(
        Node(
            id="strips/asr/segments",
            kind="asr.segment",
            scope=M,
            data={},
            edges=(
                Edge(target="audio.wav", role="audio"),
                Edge(target="recipes/asr", scope=LIB, role="recipe"),
            ),
        )
    )
    tx.commit()
    pinned = store.snapshot(M, LIB)  # holds both manifests → snapshot semantics
    latest = store.snapshot(M)  # holds only M → reads LIB live
    h_pinned = store.composed_hash(pinned, M, "strips/asr/segments")
    assert store.composed_hash(latest, M, "strips/asr/segments") == h_pinned
    tx = store.transaction(LIB)
    tx.put(Node(id="recipes/asr", kind="recipe.template", scope=LIB, data={"v": 2}))
    tx.commit()
    assert store.composed_hash(pinned, M, "strips/asr/segments") == h_pinned
    assert store.composed_hash(latest, M, "strips/asr/segments") != h_pinned


def test_composed_hash_names_dangling_edges_and_cycles():
    store = Store()
    tx = store.transaction(M)
    tx.put(Node(id="a", kind="k", scope=M, data=1, edges=(Edge(target="missing"),)))
    tx.commit()
    with pytest.raises(DanglingEdge):
        store.composed_hash(store.snapshot(M), M, "a")
    tx = store.transaction(M)
    tx.put(Node(id="x", kind="k", scope=M, data=1, edges=(Edge(target="y"),)))
    tx.put(Node(id="y", kind="k", scope=M, data=1, edges=(Edge(target="x"),)))
    tx.commit()
    with pytest.raises(Cycle):
        store.composed_hash(store.snapshot(M), M, "x")


def test_composed_hash_walks_a_chain_deeper_than_the_recursion_limit():
    """A strip whose every window reads the previous one is an N-deep chain.
    Found by a bench, not foresight: the first walk was recursive and died at
    ~1,000. N is unbounded, so the walk must not be."""
    import sys

    depth = sys.getrecursionlimit() * 5
    store = Store()
    tx = store.transaction(M)
    tx.put(Node(id="w/0", kind="window", scope=M, data={"i": 0}))
    for i in range(1, depth):
        tx.put(Node(id=f"w/{i}", kind="window", scope=M, data={"i": i}, edges=(Edge(target=f"w/{i-1}", role="prev"),)))
    tx.commit()
    snap = store.snapshot(M)
    h = store.composed_hash(snap, M, f"w/{depth-1}")
    assert h == store.composed_hash(snap, M, f"w/{depth-1}")
    # a diamond and a shared tail resolve once each, not once per path
    tx = store.transaction(M)
    tx.put(Node(id="fan", kind="k", scope=M, data=0, edges=(Edge(target="w/10", role="a"), Edge(target="w/9", role="b"))))
    m = tx.commit()
    assert store.composed_hash(store.snapshot(M), M, "fan") != h


# --- decoration direction ------------------------------------------------------


def test_functional_nodes_may_not_bond_to_decoration():
    store = Store()
    tx = store.transaction(M)
    with pytest.raises(ValueError, match="decoration"):
        tx.put(
            Node(
                id="strips/asr/segments",
                kind="asr.segment",
                scope=M,
                data={},
                edges=(Edge(target="hallmarks/asr-2026-08-29", role="proof"),),
            )
        )
    # The other direction is the point: decoration cites the functional node.
    tx.put(_asr())
    tx.put(
        Node(
            id="hallmarks/asr-2026-08-29",
            kind="hallmark",
            scope=M,
            data={"outcome": "exact"},
            edges=(Edge(target="strips/asr/segments"),),
        )
    )
    m = tx.commit()
    assert isinstance(store.get(m, "hallmarks/asr-2026-08-29"), Node)
    # And attaching the label did not move the labelled node's identity.
    assert m.hash_of("strips/asr/segments") == _asr().content_hash()
