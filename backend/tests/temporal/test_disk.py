"""The disk tier: persistence, sparse load, write-through, eviction,
integrity."""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from nodecules.core.disk import Corrupt, DiskBacking, load
from nodecules.core.replica import sync
from nodecules.core.store import Edge, Node, Store, envelope_id, make_envelope

M = "project/stenota/meeting/abc123"


def _graph(store: Store) -> None:
    tx = store.transaction(M, author="alice")
    tx.put(Node(id="audio.wav", kind="raw.audio", scope=M, data={"path": "meeting.wav"}))
    tx.put(Node(id="strips/asr/segments", kind="asr.segment", scope=M, data={"segments": [0, 1, 2]}, edges=(Edge(target="audio.wav", role="audio"),)))
    tx.commit("first cook")
    tx = store.transaction(M, author="bob")
    tx.put(Node(id="strips/diar/segments", kind="diar.segment", scope=M, data={"turns": []}, edges=(Edge(target="audio.wav", role="audio"),)))
    tx.commit("diar")


def test_round_trip_reproduces_heads_history_and_roots(tmp_path: Path):
    a = Store()
    _graph(a)
    a.attach(DiskBacking(tmp_path))  # existing content is written through
    b = load(tmp_path)
    assert b.scopes() == (M,)
    assert b.current(M).content_hash() == a.current(M).content_hash()
    assert [m.seq for m in b.history(M)] == [2, 1, 0]
    assert [m.author for m in b.history(M)] == ["bob", "alice", ""]
    assert b.current(M).root == a.current(M).root  # the trie rebuilt from entries has the same root
    got = b.get(b.current(M), "strips/asr/segments")
    assert isinstance(got, Node) and got.data == {"segments": [0, 1, 2]} and [e.target for e in got.edges] == ["audio.wav"]


def test_bodies_stay_on_disk_until_read_and_can_be_evicted_losslessly(tmp_path: Path):
    a = Store()
    _graph(a)
    a.attach(DiskBacking(tmp_path))
    b = load(tmp_path)
    h = b.current(M).hash_of("audio.wav")
    assert b.residency(h) == "disk" and b.has_body(h)
    assert isinstance(b.get(b.current(M), "audio.wav"), Node)
    assert b.residency(h) == "ram"
    b.evict(h)
    assert b.residency(h) == "disk"
    assert b.get_by_hash(h).data == {"path": "meeting.wav"} and b.residency(h) == "ram"
    # composed hashes work without ever loading bodies: skeletons come from disk
    c = load(tmp_path)
    c.composed_hash(c.snapshot(M), M, "strips/asr/segments")
    assert c.residency(c.current(M).hash_of("audio.wav")) == "disk"
    with pytest.raises(ValueError):
        Store().evict("nope")


def test_a_commit_is_on_disk_before_the_head_moves(tmp_path: Path):
    backing = DiskBacking(tmp_path)
    a = Store()
    a.attach(backing)
    tx = a.transaction(M, author="alice")
    tx.put(Node(id="x", kind="k", scope=M, data=1))
    m = tx.commit("durable")
    assert backing.get_manifest(m.content_hash()) is not None
    assert backing.heads() == {M: [m.content_hash()]}
    assert backing.has_body(m.hash_of("x"))
    # a brand-new process sees exactly that
    assert load(tmp_path).current(M).content_hash() == m.content_hash()


def test_prune_with_a_backing_releases_the_bytes_everywhere(tmp_path: Path):
    a = Store()
    _graph(a)
    a.attach(DiskBacking(tmp_path))
    asr = a.get(a.current(M), "strips/asr/segments")
    tx = a.transaction(M, author="alice")
    tx.put(make_envelope(asr, recipe={"realization": "r"}, inputs={}))
    tx.commit()
    h = asr.content_hash()
    a.prune(h)
    assert a.residency(h) == "pruned" and not a.backing.has_body(h)
    b = load(tmp_path)
    got = b.get(b.current(M), "strips/asr/segments")
    assert got.__class__.__name__ == "Absent" and got.rebuild_via == envelope_id("strips/asr/segments")
    assert [e.target for e in got.edges] == ["audio.wav"]  # the skeleton survived on disk


def test_tampering_is_refused(tmp_path: Path):
    a = Store()
    _graph(a)
    a.attach(DiskBacking(tmp_path))
    h = a.current(M).hash_of("audio.wav")
    body = tmp_path / "bodies" / f"{h}.json"
    raw = json.loads(body.read_text())
    raw["data"]["path"] = "evil.wav"
    body.write_text(json.dumps(raw))
    b = load(tmp_path)
    with pytest.raises(Corrupt):
        b.get(b.current(M), "audio.wav")
    mh = a.current(M).content_hash()
    mp = tmp_path / "manifests" / f"{mh}.json"
    raw = json.loads(mp.read_text())
    raw["entries"].append(["smuggled", h])
    mp.write_text(json.dumps(raw))
    with pytest.raises(Corrupt):
        load(tmp_path)


def test_two_backed_replicas_sync_and_survive_reload(tmp_path: Path):
    a = Store()
    a.attach(DiskBacking(tmp_path / "a"))
    _graph(a)
    b = Store()
    b.attach(DiskBacking(tmp_path / "b"))
    sync(a, b, M)
    tx = b.transaction(M, author="bob")
    tx.put(Node(id="from-b", kind="k", scope=M, data=1))
    tx.commit()
    sync(a, b, M)
    assert a.current(M).content_hash() == b.current(M).content_hash()
    a2, b2 = load(tmp_path / "a"), load(tmp_path / "b")
    assert a2.current(M).content_hash() == b2.current(M).content_hash() == a.current(M).content_hash()
    assert isinstance(a2.get(a2.current(M), "from-b"), Node)


def test_a_shared_body_answers_under_the_name_asked():
    s = Store()
    tx = s.transaction(M)
    tx.put(Node(id="audio.wav", kind="raw.audio", scope=M, data={"path": "x"}))
    tx.put(Node(id="audio-copy.wav", kind="raw.audio", scope=M, data={"path": "x"}))
    m = tx.commit()
    assert s.get(m, "audio-copy.wav").id == "audio-copy.wav"
    assert s.get(m, "audio.wav").id == "audio.wav"


def test_a_store_directory_merged_by_git_attaches_to_one_head(tmp_path: Path):
    """Two machines commit to copies of one store directory; a git merge of
    the two copies unions the files (every name is a hash, so nothing
    conflicts) and leaves two candidate heads; attach merges them."""
    import shutil

    a = Store()
    a.attach(DiskBacking(tmp_path / "shared"))
    _graph(a)
    shutil.copytree(tmp_path / "shared", tmp_path / "spark")
    spark = load(tmp_path / "spark")
    tx = spark.transaction(M, author="spark")
    tx.put(Node(id="answer", kind="k", scope=M, data={"summary": "from the model"}))
    tx.commit("fulfil")
    tx = a.transaction(M, author="cloud")
    tx.put(Node(id="question", kind="k", scope=M, data={"q": "?"}))
    tx.commit("ask")
    # "git merge": union of both trees, file by file, never editing a file
    for src in (tmp_path / "shared", tmp_path / "spark"):
        for p in src.rglob("*"):
            if p.is_file():
                dst = tmp_path / "merged" / p.relative_to(src)
                dst.parent.mkdir(parents=True, exist_ok=True)
                if not dst.exists():
                    shutil.copy2(p, dst)
    assert len(DiskBacking(tmp_path / "merged").heads()[M]) == 2
    merged = load(tmp_path / "merged")
    head = merged.current(M)
    assert head.merge_parent is not None
    assert isinstance(merged.get(head, "answer"), Node) and isinstance(merged.get(head, "question"), Node)
    assert DiskBacking(tmp_path / "merged").heads()[M] == [head.content_hash()]  # retired both candidates
    # both machines loading the merged directory agree
    assert load(tmp_path / "merged").current(M).content_hash() == head.content_hash()


def test_a_scope_whose_head_files_went_missing_is_recovered_from_its_manifests_out_loud(tmp_path):
    """Found on the Spark, 2026-09-19: the repo's gitignore swallowed
    heads/lib/, so the lib manifest arrived without its head and the first
    symptom was a DanglingEdge two layers up. Now the missing head is
    recovered from the manifest DAG (its leaves), written back, and said
    with a warning naming the scope."""
    import shutil
    import warnings as _w

    root = tmp_path / "store"
    store = Store()
    store.attach(DiskBacking(root))
    tx = store.transaction("lib", author="dev")
    tx.put(Node(id="recipes/a", kind="recipe", scope="lib", data={"v": 1}))
    tx.commit("first")
    tx = store.transaction("lib", author="dev")
    tx.put(Node(id="recipes/a", kind="recipe", scope="lib", data={"v": 2}))
    latest = tx.commit("second")
    tx = store.transaction("app", author="dev")
    tx.put(Node(id="x", kind="k", scope="app", data=1))
    tx.commit("app")
    shutil.rmtree(root / "heads" / "lib")  # what a gitignored directory looks like on the other machine

    with _w.catch_warnings(record=True) as caught:
        _w.simplefilter("always")
        reloaded = load(root)
    msgs = [str(c.message) for c in caught if issubclass(c.category, RuntimeWarning)]
    assert len(msgs) == 1 and "'lib'" in msgs[0] and "2 manifest(s)" in msgs[0] and "recovered 1 head(s)" in msgs[0]
    assert reloaded.current("lib").content_hash() == latest.content_hash()
    assert reloaded.get(reloaded.current("lib"), "recipes/a").data == {"v": 2}
    assert (root / "heads" / "lib" / latest.content_hash()).exists()  # written back
    assert set(reloaded.scopes()) == {"lib", "app"}
    with _w.catch_warnings(record=True) as again:
        _w.simplefilter("always")
        load(root)
    assert not [c for c in again if issubclass(c.category, RuntimeWarning)]  # healed, so quiet
