"""The replica transport: a store served over HTTP, and a pull that brings
another replica up to date with it.

**No authentication and no encryption.** Anyone who can reach the port can
read every object the store holds. The server binds loopback unless told
otherwise; between machines the hop is an ssh tunnel (`ssh -L` one way,
`-R` the other), which authenticates and encrypts it without this module
owning either. Signed manifests and grants are separate slices.

The wire is the read half of `Backing` (`store.py`): candidate heads by
scope, and manifests, bodies, and skeletons by content hash, each in the
JSON shape the disk tier writes (`disk.py`'s codec). Everything is
content-addressed, so integrity is the hash and nothing else: the client
re-checks every object it receives and refuses a peer that sends anything
that does not hash to its name (`Corrupt`), before anything is merged.

Sync is **pull**. `pull(store, peer)` asks the peer for its heads, asks
it to walk the heads' manifest DAGs down to what the store already has
(`/walk`, one request however deep the history; against a peer without
`/walk`, one batched request per depth, all scopes together), fetches the
bodies those manifests bind that the store lacks (one batched request),
imports parents before children, and
brings each head in with `replica.merge_head`, the rule `attach` uses for a
git-merged directory. Nothing is pushed into a store; which heads it takes
stays local.

A directory written while it is served (a fulfil committing, a git checkout
landing) can show a head before its manifest, or a manifest before its
bodies. Such a head is not merged: the report lists it under `retry` and
the next pull takes it. A body the peer pruned on purpose (its skeleton is
still there) does not block a pull, as with `replica.transfer`; the body
is simply not copied. A scope's genesis manifest is never asked for: it is
the same hash in every store, and a disk store attached before the scope's
first commit never wrote it, so the pull mints it locally.

Endpoints:

    GET  /heads            {"heads": {scope: [hash, ...]}}
    GET  /manifest/<hash>  one manifest, or 404
    GET  /body/<hash>      one body, or 404
    GET  /skeleton/<hash>  one skeleton (kind and edges), or 404
    POST /objects          {"manifests": [hash...], "bodies": [hash...]}
                           -> {"manifests": {hash: obj|null}, "bodies": {hash: obj|null},
                               "skeletons": {hash: obj}}   (for bodies the peer lacks)
    POST /walk             {"heads": [hash...], "have": [hash...], "limit": n}
                           -> {"manifests": {hash: obj}, "missing": [hash...], "truncated": bool}
                           the manifests reachable from heads, breadth first, stopping at
                           anything in have, at most limit of them; missing are the ones
                           reached that cannot be served yet

No clock here: the caller measures time (`handoff/transport.py` does).
"""

from __future__ import annotations

import heapq
import http.client
from collections import deque
import json
import re
import threading
from dataclasses import dataclass, field
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from typing import Dict, Iterable, List, Optional, Set, Tuple
from urllib.parse import unquote, urlparse

from .disk import Corrupt, decode_body, decode_manifest, decode_skeleton, encode_body, encode_manifest, encode_skeleton
from .replica import merge_head
from .store import Backing, Edge, Manifest, Node, Store

HASH = re.compile(r"^[0-9a-f]{64}$")
MAX_BATCH = 10_000  # hashes per /objects request, and the most manifests one /walk returns
WALK_LIMIT = 1_000  # manifests per /walk response unless the caller asks otherwise
HAVE_DEPTH = 64  # first-parent ancestors of each local head offered as `have`
MAX_REQUEST = 4 * 1024 * 1024  # bytes of request body the server reads


class TransportError(Exception):
    """The peer answered with something other than an object or a 404."""


# --- the server -----------------------------------------------------------------------


def _quietly(read, h):
    """An object, or None when it is absent or cannot be read right now (a
    file mid-write, or one the backing itself refuses as corrupt)."""
    try:
        return read(h)
    except (Corrupt, ValueError, KeyError, OSError):
        return None


def _manifest_json(b: Backing, h: str) -> Optional[dict]:
    m = _quietly(b.get_manifest, h)
    return None if m is None else encode_manifest(m)


def _body_json(b: Backing, h: str) -> Optional[dict]:
    n = _quietly(b.get_body, h)
    return None if n is None else encode_body(n)


def _skeleton_json(b: Backing, h: str) -> Optional[dict]:
    sk = _quietly(b.get_skeleton, h)
    return None if sk is None else encode_skeleton(*sk)


_SINGLE = {"manifest": _manifest_json, "body": _body_json, "skeleton": _skeleton_json}


def _walk(b: Backing, heads: List[str], have: Set[str], limit: int) -> dict:
    """Manifests reachable from `heads`, breadth first, stopping at `have`."""
    out: Dict[str, dict] = {}
    missing: List[str] = []
    seen = set(have)
    queue = deque(heads)
    truncated = False
    while queue:
        h = queue.popleft()
        if h in seen:
            continue
        if len(out) >= limit:
            truncated = True
            break
        seen.add(h)
        m = _quietly(b.get_manifest, h)
        if m is None:
            missing.append(h)
            continue
        out[h] = encode_manifest(m)
        for p in (m.parent, m.merge_parent):
            if p and p not in seen:
                queue.append(p)
    return {"manifests": out, "missing": missing, "truncated": truncated}


def _hashes(xs) -> bool:
    return isinstance(xs, list) and all(isinstance(h, str) and HASH.match(h) for h in xs)


class _Handler(BaseHTTPRequestHandler):
    protocol_version = "HTTP/1.1"  # keep-alive: a pull is several requests on one connection
    disable_nagle_algorithm = True  # headers and body are two writes; Nagle held the second ~40 ms per request

    def log_message(self, *args) -> None:  # quiet; the caller logs what it wants
        pass

    def _send(self, code: int, obj) -> None:
        data = json.dumps(obj, sort_keys=True, separators=(",", ":")).encode("utf-8")
        self.send_response(code)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(data)))
        self.end_headers()
        self.wfile.write(data)

    def do_GET(self) -> None:
        backing: Backing = self.server.backing  # type: ignore[attr-defined]
        parts = self.path.split("?", 1)[0].strip("/").split("/")
        if parts == ["heads"]:
            return self._send(200, {"heads": backing.heads()})
        if len(parts) == 2 and parts[0] in _SINGLE:
            h = unquote(parts[1])
            if not HASH.match(h):
                return self._send(400, {"error": "not a content hash"})
            obj = _SINGLE[parts[0]](backing, h)
            return self._send(200, obj) if obj is not None else self._send(404, {"error": "absent"})
        return self._send(404, {"error": "no such endpoint"})

    def do_POST(self) -> None:
        backing: Backing = self.server.backing  # type: ignore[attr-defined]
        path = self.path.split("?", 1)[0]
        length = int(self.headers.get("Content-Length") or 0)
        if length > MAX_REQUEST:
            self.close_connection = True  # the body stays unread, so this connection cannot carry another request
            return self._send(413, {"error": "request too large"})
        raw = self.rfile.read(length)  # read before any answer, so a kept-alive connection stays in step
        if path not in ("/objects", "/walk"):
            return self._send(404, {"error": "no such endpoint"})
        try:
            req = json.loads(raw or b"{}")
            if not isinstance(req, dict):
                raise TypeError
        except (ValueError, TypeError):
            return self._send(400, {"error": "expected a JSON object"})
        if path == "/walk":
            heads, have, limit = req.get("heads", []), req.get("have", []), req.get("limit", WALK_LIMIT)
            if not (_hashes(heads) and _hashes(have)) or len(heads) > MAX_BATCH or not isinstance(limit, int) or isinstance(limit, bool) or not 0 < limit <= MAX_BATCH:
                return self._send(400, {"error": "heads and have are lists of hashes; limit is 1..%d" % MAX_BATCH})
            return self._send(200, _walk(backing, heads, set(have), limit))
        ms, bs = req.get("manifests", []), req.get("bodies", [])
        if not (isinstance(ms, list) and isinstance(bs, list)):
            return self._send(400, {"error": "expected {manifests: [...], bodies: [...]}"})
        if len(ms) + len(bs) > MAX_BATCH or not all(isinstance(h, str) and HASH.match(h) for h in ms + bs):
            return self._send(400, {"error": "hashes only, at most %d" % MAX_BATCH})
        out = {
            "manifests": {h: _manifest_json(backing, h) for h in ms},
            "bodies": {h: _body_json(backing, h) for h in bs},
            "skeletons": {},
        }
        for h in bs:
            if out["bodies"][h] is None:
                sk = _skeleton_json(backing, h)
                if sk is not None:
                    out["skeletons"][h] = sk
        return self._send(200, out)


class Served:
    """A running server: `url`, and `close()` to stop it."""

    def __init__(self, httpd: ThreadingHTTPServer, thread: threading.Thread) -> None:
        self._httpd = httpd
        self._thread = thread
        self.host, self.port = httpd.server_address[0], httpd.server_address[1]
        self.url = f"http://{self.host}:{self.port}"

    def close(self) -> None:
        self._httpd.shutdown()
        self._httpd.server_close()
        self._thread.join(timeout=5)


def serve(backing: Backing, host: str = "127.0.0.1", port: int = 0) -> Served:
    """Serve a backing's read half on a thread. Loopback by default; a
    LAN address is a deliberate choice (there is no auth here). Reads go
    to the backing on every request, so a writer sharing the directory is
    seen at once."""
    httpd = ThreadingHTTPServer((host, port), _Handler)
    httpd.daemon_threads = True
    httpd.backing = backing  # type: ignore[attr-defined]
    thread = threading.Thread(target=httpd.serve_forever, kwargs={"poll_interval": 0.05}, name=f"transport:{httpd.server_address[1]}", daemon=True)
    thread.start()
    return Served(httpd, thread)


# --- the client -----------------------------------------------------------------------


class Peer(Backing):
    """The read half of `Backing` over HTTP. Writes are not offered: sync is
    pull, and nothing is pushed into another store. Counts its requests and
    the bytes it received, for measurement."""

    def __init__(self, url: str, timeout: float = 30.0) -> None:
        u = urlparse(url)
        if u.scheme != "http" or not u.hostname:
            raise ValueError(f"expected http://host:port, got {url!r}")
        self.url = url
        self._host, self._port = u.hostname, u.port or 80
        self._timeout = timeout
        self._conn: Optional[http.client.HTTPConnection] = None
        self.requests = 0
        self.bytes = 0
        self.walks = True  # until the peer says it has no /walk

    def _request(self, method: str, path: str, body: Optional[dict] = None) -> Optional[dict]:
        data = None if body is None else json.dumps(body).encode("utf-8")
        headers = {"Content-Type": "application/json"} if data is not None else {}
        for attempt in (0, 1):  # one retry: a kept-alive connection the server has since closed
            conn = self._conn or http.client.HTTPConnection(self._host, self._port, timeout=self._timeout)
            try:
                conn.request(method, path, body=data, headers=headers)
                resp = conn.getresponse()
                raw = resp.read()
            except (http.client.HTTPException, OSError):
                conn.close()
                self._conn = None
                if attempt:
                    raise
                continue
            self._conn = conn
            self.requests += 1
            self.bytes += len(raw)
            if resp.status == 404:
                return None
            if resp.status != 200:
                raise TransportError(f"{method} {path}: {resp.status} {raw[:200]!r}")
            return json.loads(raw)
        return None  # pragma: no cover - the loop returns or raises

    def close(self) -> None:
        if self._conn is not None:
            self._conn.close()
            self._conn = None

    # -- the read half of Backing --------------------------------------------------------

    def heads(self) -> Dict[str, List[str]]:
        got = self._request("GET", "/heads")
        return {} if got is None else got["heads"]

    def get_manifest(self, content_hash: str) -> Optional[Manifest]:
        raw = self._request("GET", f"/manifest/{content_hash}")
        return None if raw is None else decode_manifest(raw, content_hash)

    def get_body(self, content_hash: str) -> Optional[Node]:
        raw = self._request("GET", f"/body/{content_hash}")
        return None if raw is None else decode_body(raw, content_hash)

    def has_body(self, content_hash: str) -> bool:
        return self.get_body(content_hash) is not None

    def get_skeleton(self, content_hash: str) -> Optional[Tuple[str, Tuple[Edge, ...]]]:
        raw = self._request("GET", f"/skeleton/{content_hash}")
        return None if raw is None else decode_skeleton(raw)

    # -- batched ---------------------------------------------------------------------------

    def walk(
        self, heads: Iterable[str], have: Iterable[str], limit: int = WALK_LIMIT
    ) -> Optional[Tuple[Dict[str, Manifest], Set[str], bool]]:
        """The manifests reachable from `heads` that are not behind `have`,
        each checked against its hash; the ones the peer reached and could
        not serve; and whether the response stopped at `limit`. None when
        the peer has no /walk (an older server), and it is not asked again."""
        if not self.walks:
            return None
        resp = self._request("POST", "/walk", {"heads": list(heads), "have": list(have), "limit": limit})
        if resp is None:
            self.walks = False
            self.close()  # a server from before /walk answers 404 without reading the body: start a fresh connection
            return None
        got = {h: decode_manifest(raw, h) for h, raw in resp["manifests"].items()}
        return got, set(resp.get("missing", [])), bool(resp.get("truncated"))

    def fetch(
        self, manifests: Iterable[str] = (), bodies: Iterable[str] = ()
    ) -> Tuple[Dict[str, Optional[Manifest]], Dict[str, Optional[Node]], Set[str]]:
        """Many objects per request. Returns manifests and bodies by hash
        (None where the peer could not serve one) and the set of bodies the
        peer lacks but holds a skeleton for (pruned there on purpose).
        Every object is checked against its hash; a mismatch raises."""
        ms, bs = list(manifests), list(bodies)
        got_m: Dict[str, Optional[Manifest]] = {}
        got_b: Dict[str, Optional[Node]] = {}
        pruned: Set[str] = set()
        chunks = [(ms[i : i + MAX_BATCH], []) for i in range(0, len(ms), MAX_BATCH)]
        chunks += [([], bs[i : i + MAX_BATCH]) for i in range(0, len(bs), MAX_BATCH)]
        for cm, cb in chunks:
            resp = self._request("POST", "/objects", {"manifests": cm, "bodies": cb})
            if resp is None:
                raise TransportError("POST /objects: 404")
            for h in cm:
                raw = resp["manifests"].get(h)
                got_m[h] = None if raw is None else decode_manifest(raw, h)
            for h in cb:
                raw = resp["bodies"].get(h)
                got_b[h] = None if raw is None else decode_body(raw, h)
                if raw is None and h in resp.get("skeletons", {}):
                    pruned.add(h)
        return got_m, got_b, pruned


# --- pull ---------------------------------------------------------------------------------


@dataclass
class PullReport:
    """What one pull did. `heads` is this store's head per scope afterwards;
    `retry` lists, per scope, the peer's heads not merged this round because
    the peer could not yet serve all of them (the next pull takes them)."""

    heads: Dict[str, str] = field(default_factory=dict)
    manifests: int = 0
    bodies: int = 0
    retry: Dict[str, List[str]] = field(default_factory=dict)
    requests: int = 0
    bytes: int = 0


def _parents_first(ms: Dict[str, Manifest]) -> List[str]:
    """The fetched manifests in an order where every parent among them comes
    before its children (by seq, then hash, among those that are ready)."""
    pending: Dict[str, int] = {}
    children: Dict[str, List[str]] = {}
    for h, m in ms.items():
        ps = [p for p in (m.parent, m.merge_parent) if p and p in ms]
        pending[h] = len(ps)
        for p in ps:
            children.setdefault(p, []).append(h)
    ready = [(ms[h].seq, h) for h, n in pending.items() if n == 0]
    heapq.heapify(ready)
    out: List[str] = []
    while ready:
        _, h = heapq.heappop(ready)
        out.append(h)
        for c in children.get(h, ()):
            pending[c] -= 1
            if pending[c] == 0:
                heapq.heappush(ready, (ms[c].seq, c))
    return out


def _genesis(scope: str) -> Manifest:
    """The scope's empty first manifest, as every store mints it."""
    return Store().current(scope)


def _have(store: Store, scopes: Iterable[str]) -> List[str]:
    """What to tell a peer we hold, so its walk stops early: each scope's
    genesis, its head, and the head's recent first-parent ancestors. A
    walk that goes past these over-fetches, which costs bytes, not
    correctness: what we already hold is skipped on arrival."""
    out: List[str] = []
    for scope in scopes:
        out.append(_genesis(scope).content_hash())
        if scope not in store.scopes():
            continue
        m: Optional[Manifest] = store.current(scope)
        for _ in range(HAVE_DEPTH):
            if m is None:
                break
            out.append(m.content_hash())
            m = store.manifest(m.parent) if m.parent else None
    return sorted(set(out))


def pull(
    store: Store,
    peer: Peer,
    *,
    scopes: Optional[Iterable[str]] = None,
    author: str = "pull",
    walk_limit: int = WALK_LIMIT,
) -> PullReport:
    """Bring `store` up to date with `peer` for every scope the peer has
    (or only `scopes`). Raises `Corrupt` if the peer sends an object that
    does not hash to its name, before anything is merged; anything the
    peer cannot serve yet becomes a `retry` instead of an import.

    Every request is a round trip, so: one request for the heads; the
    peer walks the manifest DAGs (one /walk per `walk_limit` manifests);
    one request for all the bodies. Against a peer without /walk, one
    request per DAG depth for the manifests of all scopes together."""
    report = PullReport()
    r0, b0 = peer.requests, peer.bytes
    wanted = None if scopes is None else set(scopes)
    remote = {s: hs for s, hs in peer.heads().items() if wanted is None or s in wanted}
    for scope in remote:
        genesis = _genesis(scope)
        if store.manifest(genesis.content_hash()) is None:
            store.import_manifest(genesis)

    # 1. The manifests we lack, down to the first ones we have: the peer walks them.
    fetched: Dict[str, Manifest] = {}
    unserved: Set[str] = set()
    frontier = sorted({h for hs in remote.values() for h in hs if store.manifest(h) is None})

    def parents_to_fetch(ms: Iterable[Manifest]) -> List[str]:
        return sorted({
            p for m in ms for p in (m.parent, m.merge_parent)
            if p and store.manifest(p) is None and p not in fetched and p not in unserved
        })

    have = _have(store, remote) if frontier else []
    while frontier:
        walked = peer.walk(frontier, have, walk_limit)
        if walked is None:
            break  # no /walk there: the per-depth path below
        got, missing, truncated = walked
        new = {h: m for h, m in got.items() if store.manifest(h) is None and h not in fetched}
        fetched.update(new)
        unserved |= missing
        frontier = parents_to_fetch(new.values())
        if not new and not missing:
            break  # the peer walked nothing for a frontier it named: per-depth below
    # Per depth: a peer without /walk, or whatever its walk left.
    while frontier:
        got, _, _ = peer.fetch(manifests=frontier)
        nxt: Set[str] = set()
        for h in frontier:
            m = got.get(h)
            if m is None:
                unserved.add(h)
                continue
            fetched[h] = m
            for p in (m.parent, m.merge_parent):
                if p and store.manifest(p) is None and p not in fetched and p not in unserved:
                    nxt.add(p)
        frontier = sorted(nxt)

    # 2. The bodies those manifests bind that we lack: one request.
    want = sorted({b for m in fetched.values() for _, b in m.entries() if not store.has_body(b)})
    absent: Set[str] = set()
    if want:
        _, bodies, pruned = peer.fetch(bodies=want)
        for h in want:
            node = bodies.get(h)
            if node is not None:
                store.import_body(node)
                report.bodies += 1
            elif h not in pruned:
                absent.add(h)  # not written there yet: the manifests binding it wait

    # 3. Parents before children; a manifest waits if a parent or a body is not here.
    for h in _parents_first(fetched):
        m = fetched[h]
        parents_here = all(p is None or store.manifest(p) is not None for p in (m.parent, m.merge_parent))
        bodies_here = not any(b in absent for _, b in m.entries())
        if parents_here and bodies_here:
            store.import_manifest(m)
            report.manifests += 1

    # 4. Head acceptance is local: merge each candidate we now hold.
    for scope in sorted(remote):
        for h in remote[scope]:
            m = store.manifest(h)
            if m is None:
                report.retry.setdefault(scope, []).append(h)
                continue
            merge_head(store, scope, m, author=author)
        if scope in store.scopes():
            report.heads[scope] = store.current(scope).content_hash()

    report.requests = peer.requests - r0
    report.bytes = peer.bytes - b0
    return report


__all__ = ["HASH", "MAX_BATCH", "Peer", "PullReport", "Served", "TransportError", "pull", "serve"]
