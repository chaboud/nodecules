"""The replica transport: two stores on loopback in one process, a server
over a store directory, and a pull that converges them.

Each case is something the wire meets between real machines: an empty
replica, nothing new, one new commit, edits on both sides, a peer that
lies, and a directory caught mid-write (a head before its manifest, a
manifest before its bodies), which must become a retry rather than a bad
import."""

from __future__ import annotations

import json
import urllib.error
import urllib.request
from pathlib import Path

import pytest

from nodecules.core.disk import Corrupt, DiskBacking, load
from nodecules.core.store import Edge, Node, Store, make_envelope
from nodecules.core.transport import Peer, pull, serve

M = "meetings/standup"


def _graph(store: Store) -> None:
    tx = store.transaction(M, author="alice")
    tx.put(Node(id="audio.wav", kind="raw.audio", scope=M, data={"path": "meeting.wav"}))
    tx.put(Node(id="strips/asr/segments", kind="asr.segment", scope=M, data={"segments": [0, 1, 2]}, edges=(Edge(target="audio.wav", role="audio"),)))
    tx.commit("first cook")
    tx = store.transaction(M, author="bob")
    tx.put(Node(id="strips/diar/segments", kind="diar.segment", scope=M, data={"turns": []}, edges=(Edge(target="audio.wav", role="audio"),)))
    tx.commit("diar")


def _put(store: Store, name: str, data, author: str = "kid", scope: str = M) -> None:
    tx = store.transaction(scope, author=author)
    tx.put(Node(id=name, kind="k", scope=scope, data=data))
    tx.commit()


@pytest.fixture
def servers():
    running = []

    def start(backing):
        s = serve(backing)
        running.append(s)
        return s

    yield start
    for s in running:
        s.close()


def _old_server(backing):
    """A server from before /walk: everything else the same, /walk is a 404."""
    import threading
    from http.server import ThreadingHTTPServer

    from nodecules.core.transport import Served, _Handler

    class Old(_Handler):
        def do_POST(self):
            if self.path.startswith("/walk"):
                return self._send(404, {"error": "no such endpoint"})
            return super().do_POST()

    httpd = ThreadingHTTPServer(("127.0.0.1", 0), Old)
    httpd.backing = backing
    t = threading.Thread(target=httpd.serve_forever, kwargs={"poll_interval": 0.05}, daemon=True)
    t.start()
    return Served(httpd, t)


def _source(tmp_path: Path, name: str = "a") -> Store:
    s = Store()
    _graph(s)
    s.attach(DiskBacking(tmp_path / name))
    return s


def test_the_server_binds_loopback_by_default(tmp_path, servers):
    s = servers(DiskBacking(tmp_path / "a"))
    assert s.host == "127.0.0.1" and s.url.startswith("http://127.0.0.1:")


def test_a_pull_into_an_empty_store_reproduces_the_peers_head_and_bodies(tmp_path, servers):
    a = _source(tmp_path)
    s = servers(DiskBacking(tmp_path / "a"))
    b = Store()
    report = pull(b, Peer(s.url))
    assert b.current(M).content_hash() == a.current(M).content_hash()
    assert report.heads == {M: a.current(M).content_hash()}
    assert report.manifests == 2 and report.bodies == 3  # two commits over the wire; genesis is minted here
    got = b.get(b.current(M), "strips/asr/segments")
    assert isinstance(got, Node) and got.data == {"segments": [0, 1, 2]}
    assert [m.seq for m in b.history(M)] == [2, 1, 0]


def test_a_second_pull_with_nothing_new_moves_nothing_in_one_request(tmp_path, servers):
    _source(tmp_path)
    peer = Peer(servers(DiskBacking(tmp_path / "a")).url)
    b = Store()
    pull(b, peer)
    again = pull(b, peer)
    assert again.manifests == 0 and again.bodies == 0 and again.retry == {}
    assert again.requests == 1  # the heads, and nothing else


def test_an_incremental_pull_fetches_only_what_is_new(tmp_path, servers):
    a = _source(tmp_path)
    peer = Peer(servers(DiskBacking(tmp_path / "a")).url)
    b = Store()
    pull(b, peer)
    _put(a, "new", 1)
    report = pull(b, peer)
    assert report.manifests == 1 and report.bodies == 1
    assert b.current(M).content_hash() == a.current(M).content_hash()


def test_edits_on_both_sides_converge_when_each_pulls_from_the_other(tmp_path, servers):
    a = _source(tmp_path, "a")
    b = Store()
    b.attach(DiskBacking(tmp_path / "b"))
    sa, sb = servers(DiskBacking(tmp_path / "a")), servers(DiskBacking(tmp_path / "b"))
    pull(b, Peer(sa.url))
    _put(a, "from-a", "A", author="alice")
    _put(b, "from-b", "B", author="bob")
    pull(a, Peer(sb.url), author="a")
    pull(b, Peer(sa.url), author="b")
    assert a.current(M).content_hash() == b.current(M).content_hash()
    assert a.current(M).merge_parent is not None
    assert {n for n, _ in b.current(M).entries()} >= {"from-a", "from-b"}
    assert load(tmp_path / "b").current(M).content_hash() == a.current(M).content_hash()  # durable on b's disk


def test_parents_are_imported_before_children(tmp_path, servers):
    _source(tmp_path)
    _put(load(tmp_path / "a"), "third", 3)
    order: list = []

    class Recording(DiskBacking):
        def put_manifest(self, manifest):
            if all(m.content_hash() != manifest.content_hash() for m in order):  # merge_head re-imports; the disk ignores repeats
                order.append(manifest)
            super().put_manifest(manifest)

    b = Store()
    b.attach(Recording(tmp_path / "b"))
    pull(b, Peer(servers(DiskBacking(tmp_path / "a")).url))
    seen = set()
    for m in order:
        assert m.parent is None or m.parent in seen or b.manifest(m.parent) is not None
        seen.add(m.content_hash())
    assert [m.seq for m in order if m.scope == M] == [0, 1, 2, 3]  # all of them, oldest first


def test_a_peer_that_lies_is_refused_and_nothing_is_merged(tmp_path, servers):
    a = _source(tmp_path)

    class Liar(DiskBacking):
        def get_body(self, content_hash):
            return Node(id="x", kind="k", scope=M, data="not what you asked for")

    b = Store()
    before = b.current(M).content_hash()
    with pytest.raises(Corrupt):
        pull(b, Peer(servers(Liar(tmp_path / "a")).url))
    assert b.current(M).content_hash() == before != a.current(M).content_hash()


def test_a_head_whose_manifest_has_not_arrived_is_retried_not_merged(tmp_path, servers):
    a = _source(tmp_path)
    head = a.current(M).content_hash()
    mf = tmp_path / "a" / "manifests" / f"{head}.json"
    saved = mf.read_bytes()
    mf.unlink()  # a git checkout wrote the head file first
    peer = Peer(servers(DiskBacking(tmp_path / "a")).url)
    b = Store()
    report = pull(b, peer)
    assert report.retry == {M: [head]}
    assert b.current(M).content_hash() != head
    mf.write_bytes(saved)
    report = pull(b, peer)
    assert report.retry == {} and b.current(M).content_hash() == head


def test_a_manifest_whose_bodies_have_not_arrived_is_retried_not_merged(tmp_path, servers):
    a = _source(tmp_path)
    head = a.current(M).content_hash()
    diar = dict(a.current(M).entries())["strips/diar/segments"]
    files = [tmp_path / "a" / sub / f"{diar}.json" for sub in ("bodies", "skeletons")]
    saved = [f.read_bytes() for f in files]
    for f in files:
        f.unlink()  # the manifest arrived, its body has not
    peer = Peer(servers(DiskBacking(tmp_path / "a")).url)
    b = Store()
    report = pull(b, peer)
    assert report.retry == {M: [head]}
    assert b.current(M).content_hash() != head
    for f, data in zip(files, saved):
        f.write_bytes(data)
    report = pull(b, peer)
    assert report.retry == {} and b.current(M).content_hash() == head
    assert b.get(b.current(M), "strips/diar/segments").data == {"turns": []}


def test_a_body_pruned_on_the_peer_does_not_block_the_pull(tmp_path, servers):
    a = _source(tmp_path)
    asr = a.get(a.current(M), "strips/asr/segments")
    tx = a.transaction(M, author="alice")
    tx.put(make_envelope(asr, recipe={"realization": "r"}, inputs={}))
    tx.commit()
    a.prune(asr.content_hash())  # released on purpose: the skeleton stays
    b = Store()
    report = pull(b, Peer(servers(DiskBacking(tmp_path / "a")).url))
    assert report.retry == {} and b.current(M).content_hash() == a.current(M).content_hash()
    assert not b.has_body(asr.content_hash())


def test_only_the_scopes_asked_for_are_pulled(tmp_path, servers):
    a = _source(tmp_path)
    _put(a, "elsewhere", 1, scope="other/scope")
    b = Store()
    report = pull(b, Peer(servers(DiskBacking(tmp_path / "a")).url), scopes=[M])
    assert set(report.heads) == {M} and "other/scope" not in b.scopes()


def test_the_server_refuses_a_name_that_is_not_a_hash(tmp_path, servers):
    _source(tmp_path)
    s = servers(DiskBacking(tmp_path / "a"))
    for path in ("/body/..%2F..%2Fsecret", "/manifest/abc", "/skeleton/" + "g" * 64):
        with pytest.raises(urllib.error.HTTPError) as e:
            urllib.request.urlopen(s.url + path, timeout=5)
        assert e.value.code == 400
    req = urllib.request.Request(s.url + "/objects", data=json.dumps({"bodies": ["../x"]}).encode(), method="POST")
    with pytest.raises(urllib.error.HTTPError) as e:
        urllib.request.urlopen(req, timeout=5)
    assert e.value.code == 400


def test_single_objects_are_served_for_inspection(tmp_path, servers):
    a = _source(tmp_path)
    s = servers(DiskBacking(tmp_path / "a"))
    peer = Peer(s.url)
    head = a.current(M)
    assert peer.heads() == {M: [head.content_hash()]}
    assert peer.get_manifest(head.content_hash()).content_hash() == head.content_hash()
    body = dict(head.entries())["audio.wav"]
    assert peer.get_body(body).content_hash() == body
    assert peer.get_skeleton(body)[0] == "raw.audio"
    assert peer.get_body("0" * 64) is None


def test_a_store_attached_before_its_first_commit_pulls_whole(tmp_path, servers):
    """Attach first, commit after (how fulfil.py and seed_exchange.py work).
    Until 2026-10-01 the scope's genesis manifest was minted in memory and
    never written, so every chain on disk ended in a parent with no file;
    Store.current now writes it through (cloud, answering note 0018).
    Genesis is the same hash in every store, so the pull still mints it
    locally instead of asking, which also serves directories written
    before the fix."""
    a = Store()
    a.attach(DiskBacking(tmp_path / "a"))
    _graph(a)
    genesis = a.manifest(a.current(M).parent).parent
    assert (tmp_path / "a" / "manifests" / f"{genesis}.json").exists()  # written through on first touch
    b = Store()
    report = pull(b, Peer(servers(DiskBacking(tmp_path / "a")).url))
    assert report.retry == {} and b.current(M).content_hash() == a.current(M).content_hash()
    assert [m.seq for m in b.history(M)] == [2, 1, 0]


def test_both_ends_send_small_writes_at_once(tmp_path, servers):
    """A response is two small writes (headers, then body). With Nagle's
    algorithm on, the second waits for an ACK the client delays: measured at
    about 40 ms per batched request between the Air and the Spark, which
    made a 1,000-manifest pull take 55 s. The server turns it off; the
    client's http.client already does."""
    import socket

    from nodecules.core.transport import _Handler

    _source(tmp_path)
    peer = Peer(servers(DiskBacking(tmp_path / "a")).url)
    peer.heads()
    assert peer._conn.sock.getsockopt(socket.IPPROTO_TCP, socket.TCP_NODELAY) != 0
    assert _Handler.disable_nagle_algorithm is True


def test_a_cold_pull_costs_requests_by_depth_not_by_scope(tmp_path, servers):
    """Every request is a round trip, and between the Air and the Spark a
    round trip is ~19 ms: a pull that walked each scope on its own made 1,052
    requests for 50 scopes. Frontiers of all scopes go in one request per
    depth, and all bodies in one more."""
    a = Store()
    a.attach(DiskBacking(tmp_path / "a"))
    for k in range(12):
        for c in range(3):
            _put(a, f"n{c}", {"scope": k, "c": c}, scope=f"many/{k:02d}")
    b = Store()
    old = _old_server(DiskBacking(tmp_path / "a"))  # the per-depth path is the fallback when a peer has no /walk
    try:
        report = pull(b, Peer(old.url))
    finally:
        old.close()
    assert report.manifests == 36 and report.retry == {}
    assert report.requests == 1 + 1 + 3 + 1  # heads, the /walk that 404s, three depths, bodies
    assert all(b.current(f"many/{k:02d}").content_hash() == a.current(f"many/{k:02d}").content_hash() for k in range(12))


def _deep(tmp_path: Path, commits: int) -> Store:
    a = Store()
    a.attach(DiskBacking(tmp_path / "a"))
    for c in range(commits):
        _put(a, f"n{c}", {"c": c})
    return a


def test_a_deep_history_is_walked_in_one_request(tmp_path, servers):
    """One request per depth made a 500-manifest chain cost 503 requests
    and 9.9 s between the Air and the Spark. The server walks it instead."""
    a = _deep(tmp_path, 30)
    b = Store()
    report = pull(b, Peer(servers(DiskBacking(tmp_path / "a")).url))
    assert report.manifests == 30 and report.retry == {}
    assert report.requests == 3  # heads, one walk, bodies
    assert b.current(M).content_hash() == a.current(M).content_hash()
    assert len(list(b.history(M))) == 31


def test_an_incremental_walk_stops_at_what_the_puller_has(tmp_path, servers):
    a = _deep(tmp_path, 30)
    peer = Peer(servers(DiskBacking(tmp_path / "a")).url)
    b = Store()
    cold = pull(b, peer)
    _put(a, "more-1", 1)
    _put(a, "more-2", 2)
    report = pull(b, peer)
    assert report.manifests == 2 and report.bodies == 2 and report.requests == 3
    assert report.bytes * 4 < cold.bytes  # two (whole, P-37) manifests and two bodies, not the history again
    assert b.current(M).content_hash() == a.current(M).content_hash()


def test_a_walk_cut_short_by_its_limit_carries_on(tmp_path, servers):
    a = _deep(tmp_path, 30)
    b = Store()
    report = pull(b, Peer(servers(DiskBacking(tmp_path / "a")).url), walk_limit=10)
    assert report.manifests == 30 and report.requests == 1 + 3 + 1
    assert b.current(M).content_hash() == a.current(M).content_hash()


def test_a_server_without_walk_still_syncs_a_deep_history(tmp_path):
    a = _deep(tmp_path, 5)
    old = _old_server(DiskBacking(tmp_path / "a"))
    try:
        b = Store()
        peer = Peer(old.url)
        report = pull(b, peer)
        again = pull(b, peer)
    finally:
        old.close()
    assert report.manifests == 5 and report.requests == 1 + 1 + 5 + 1  # heads, the 404, five depths, bodies
    assert b.current(M).content_hash() == a.current(M).content_hash()
    assert again.requests == 1  # nothing new: no walk is tried at all


def test_the_walk_refuses_names_that_are_not_hashes(tmp_path, servers):
    _source(tmp_path)
    s = servers(DiskBacking(tmp_path / "a"))
    for body in ({"heads": ["../x"]}, {"heads": ["0" * 64], "have": ["nope"]}, {"heads": ["0" * 64], "limit": "many"}):
        req = urllib.request.Request(s.url + "/walk", data=json.dumps(body).encode(), method="POST")
        with pytest.raises(urllib.error.HTTPError) as e:
            urllib.request.urlopen(req, timeout=5)
        assert e.value.code == 400



def test_a_404_leaves_a_kept_alive_connection_usable(tmp_path, servers):
    """The server reads a POST's body before any answer. A server that
    answered an unknown endpoint first left the body on the connection, and
    the next request on it was parsed from that body (a 400)."""
    import http.client

    _source(tmp_path)
    s = servers(DiskBacking(tmp_path / "a"))
    conn = http.client.HTTPConnection("127.0.0.1", s.port, timeout=5)
    conn.request("POST", "/no-such-endpoint", body=json.dumps({"x": "y" * 100}).encode(), headers={"Content-Type": "application/json"})
    first = conn.getresponse()
    first.read()
    assert first.status == 404
    conn.request("GET", "/heads")
    second = conn.getresponse()
    assert second.status == 200 and M in json.loads(second.read())["heads"]
    conn.close()
