"""The inspector: look at a store, change it, choose what is live, watch
what happens. A small local web app over the substrate, standard library
only, in `demos/` because it is a consumer.

    cd backend && PYTHONPATH=. python3 ../demos/inspector/server.py --demo
    cd backend && PYTHONPATH=. python3 ../demos/inspector/server.py --store /path/to/dir

Then open http://127.0.0.1:8765/ . With --demo the store is seeded with
the chat graph and a router example on a disk-backed store under a
temporary directory (or --store), a tracker is attached, and an
inventory of realizations is loaded so "produce" works. With only
--store, the inventory is empty: you can inspect, edit, mark, and
activate, but not produce.

Does not show: several users on a network (one process, one store),
frames or the table, or a real model (the echo provider stands in).
"""

from __future__ import annotations

import argparse
import asyncio
import json
import os
import sys
import tempfile
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from typing import Any, Dict, Optional
from urllib.parse import parse_qs, urlparse

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))  # demos/
from common import EchoProvider  # noqa: E402

from nodecules.core.activation import activate, mark, marks  # noqa: E402
from nodecules.core.disk import DiskBacking  # noqa: E402
from nodecules.core.generation import PARAMS_ROLE, RECIPE_ROLE, RECIPE_TEMPLATE_KIND, Generator, Realization, router_realization  # noqa: E402
from nodecules.core.llm_realization import llm_realization  # noqa: E402
from nodecules.core.store import Edge, Node, Store, envelope_id  # noqa: E402
from nodecules.core.strip_access import AllPattern  # noqa: E402
from nodecules.core.strip_nodes import strip_append  # noqa: E402
from nodecules.core.tracking import JsonlSink, Tracker, lineage  # noqa: E402

HERE = Path(__file__).resolve().parent


async def _double(inputs, params):
    return {"v": inputs["x"]["v"] * int(params.get("factor", 2))}


def seed_demo(store: Store) -> None:
    """The chat graph plus a router example, so there is something to see."""
    lib, chat, calc = "chat/library", "chat/session-1", "calc/1"
    tx = store.transaction(lib, author="dev")
    tx.put(Node(id="recipes/assistant", kind=RECIPE_TEMPLATE_KIND, scope=lib, data={"realization": "llm.echo@1", "params": {"model": "echo", "temperature": 0.0}}))
    tx.put(Node(id="recipes/double", kind=RECIPE_TEMPLATE_KIND, scope=lib, data={"realization": "double@1", "params": {"factor": 2}}))
    tx.put(Node(id="recipes/route", kind=RECIPE_TEMPLATE_KIND, scope=lib, data={"realization": "router.first-available@1", "params": {"prefer": ["fast", "slow"]}}))
    tx.commit("recipes")
    tx = store.transaction(chat, author="dev")
    tx.put(Node(id="params/persona", kind="params", scope=chat, data={"system": "You are a helpful assistant."}))
    tx.put(Node(id="reply", kind="chat.reply", scope=chat, edges=(Edge(target="strips/messages", pattern=AllPattern(), role="messages"), Edge(target="recipes/assistant", scope=lib, role=RECIPE_ROLE), Edge(target="params/persona", role=PARAMS_ROLE))))
    tx.commit("declare the chat graph")
    mark(store, chat, "factory", note="as shipped", author="dev")
    strip_append(store, chat, "strips/messages", {"role": "user", "content": "hi"}, author="alice")
    tx = store.transaction(calc, author="dev")
    tx.put(Node(id="fast", kind="k", scope=calc, data={"v": 1}))
    tx.put(Node(id="slow", kind="k", scope=calc, data={"v": 100}))
    tx.put(Node(id="pick", kind="router", scope=calc, edges=(Edge(target="fast", role="fast", optional=True), Edge(target="slow", role="slow", optional=True), Edge(target="recipes/route", scope=lib, role=RECIPE_ROLE))))
    tx.put(Node(id="out", kind="k", scope=calc, edges=(Edge(target="pick", role="x"), Edge(target="recipes/double", scope=lib, role=RECIPE_ROLE))))
    tx.commit("declare the router example")


def demo_inventory():
    return [llm_realization(EchoProvider(), "llm.echo@1"), Realization("double@1", _double, deterministic=True), router_realization()]


class App:
    def __init__(self, store: Store, tracker: Tracker, generator: Generator, root: Path) -> None:
        self.store, self.tracker, self.generator, self.root = store, tracker, generator, root

    # -- reads -------------------------------------------------------------------

    def scopes(self) -> Any:
        out = []
        for s in self.store.scopes():
            m = self.store.current(s)
            out.append({"scope": s, "head": m.content_hash(), "seq": m.seq, "resolution": m.resolution, "names": len(m)})
        return out

    def history(self, scope: str) -> Any:
        return [self._manifest_row(m) for m in self.store.history(scope)]

    def _manifest_row(self, m) -> Dict[str, Any]:
        return {"scope": m.scope, "hash": m.content_hash(), "seq": m.seq, "author": m.author, "note": m.note, "parent": m.parent, "merge_parent": m.merge_parent, "activated": m.activated, "rebased_from": m.rebased_from, "resolution": m.resolution, "root": m.root, "names": len(m)}

    def manifest(self, h: str) -> Any:
        m = self.store.manifest(h)
        if m is None:
            return {"error": "unknown manifest"}
        entries = []
        for name, body in sorted(m.entries()):
            got = self.store.get(m, name)
            entries.append({"name": name, "hash": body, "kind": getattr(got, "kind", None), "state": type(got).__name__ if got is not None else "unbound", "residency": self.store.residency(body), "edges": [e.model_dump(mode="json") for e in getattr(got, "edges", ())]})
        return {**self._manifest_row(m), "entries": entries, "marks": marks(self.store, m)}

    def node(self, h: str, name: str) -> Any:
        m = self.store.manifest(h)
        if m is None:
            return {"error": "unknown manifest"}
        got = self.store.get(m, name)
        out: Dict[str, Any] = {"name": name, "state": type(got).__name__ if got is not None else "unbound"}
        if got is not None:
            out["node"] = got.model_dump(mode="json") if isinstance(got, Node) else got.model_dump(mode="json")
            out["content_hash"] = got.content_hash() if isinstance(got, Node) else got.content_hash
            out["residency"] = self.store.residency(out["content_hash"])
        env = self.store.get(m, envelope_id(name))
        if isinstance(env, Node):
            out["envelope"] = env.data
        out["lineage"] = lineage(self.store, m, name, depth=6)
        b = self.store.blame(m.scope, name)
        out["blame"] = {"seq": b.seq, "author": b.author, "note": b.note} if b else None
        return out

    def events(self, scope: Optional[str]) -> Any:
        evs = self.tracker.events(scope=scope) if scope else self.tracker.events()
        return [e.model_dump(mode="json") for e in evs[-200:]]

    # -- writes ------------------------------------------------------------------

    def commit(self, body: Dict[str, Any]) -> Any:
        scope, name = body["scope"], body["name"]
        tx = self.store.transaction(scope, author=body.get("author") or "inspector")
        if body.get("delete"):
            tx.delete(name)
        else:
            edges = tuple(Edge(**e) for e in body.get("edges") or [])
            tx.put(Node(id=name, kind=body.get("kind") or "k", scope=scope, data=body.get("data"), edges=edges))
        m = tx.commit(body.get("note") or f"edit {name}")
        return self._manifest_row(m)

    def produce(self, body: Dict[str, Any]) -> Any:
        g = asyncio.run(self.generator.produce(body["scope"], body["name"]))
        return {k: v for k, v in g.model_dump(mode="json").items() if k not in ("node", "envelope", "manifest")} | {"manifest": self._manifest_row(g.manifest) if g.manifest else None, "data": g.node.data if g.node else None}

    def activate(self, body: Dict[str, Any]) -> Any:
        return self._manifest_row(activate(self.store, body["scope"], body["target"], author=body.get("author") or "inspector"))

    def mark(self, body: Dict[str, Any]) -> Any:
        m = self.store.manifest(body["hash"]) if body.get("hash") else None
        return self._manifest_row(mark(self.store, body["scope"], body["label"], m, note=body.get("note", ""), author=body.get("author") or "inspector"))

    def resolution(self, body: Dict[str, Any]) -> Any:
        return self._manifest_row(self.store.set_resolution(body["scope"], body["policy"], author=body.get("author") or "inspector"))

    def evict(self, body: Dict[str, Any]) -> Any:
        self.store.evict(body["hash"])
        return {"residency": self.store.residency(body["hash"])}

    def prune(self, body: Dict[str, Any]) -> Any:
        self.store.prune(body["hash"])
        return {"residency": self.store.residency(body["hash"])}

    def say(self, body: Dict[str, Any]) -> Any:
        m = strip_append(self.store, body["scope"], body.get("strip", "strips/messages"), {"role": "user", "content": body["text"]}, author=body.get("author") or "inspector")
        return self._manifest_row(m)


def make_handler(app: App):
    class Handler(BaseHTTPRequestHandler):
        def _json(self, payload: Any, status: int = 200) -> None:
            data = json.dumps(payload, default=str).encode()
            self.send_response(status)
            self.send_header("Content-Type", "application/json")
            self.send_header("Content-Length", str(len(data)))
            self.end_headers()
            self.wfile.write(data)

        def do_GET(self) -> None:  # noqa: N802
            u = urlparse(self.path)
            q = {k: v[0] for k, v in parse_qs(u.query).items()}
            try:
                if u.path == "/":
                    html = (HERE / "index.html").read_bytes()
                    self.send_response(200)
                    self.send_header("Content-Type", "text/html; charset=utf-8")
                    self.send_header("Content-Length", str(len(html)))
                    self.end_headers()
                    self.wfile.write(html)
                elif u.path == "/api/scopes":
                    self._json(app.scopes())
                elif u.path == "/api/history":
                    self._json(app.history(q["scope"]))
                elif u.path == "/api/manifest":
                    self._json(app.manifest(q["hash"]))
                elif u.path == "/api/node":
                    self._json(app.node(q["hash"], q["name"]))
                elif u.path == "/api/events":
                    self._json(app.events(q.get("scope")))
                elif u.path == "/api/where":
                    self._json({"store": str(app.root)})
                else:
                    self._json({"error": "not found"}, 404)
            except Exception as exc:  # the inspector must not die on a bad request
                self._json({"error": f"{type(exc).__name__}: {exc}"}, 400)

        def do_POST(self) -> None:  # noqa: N802
            u = urlparse(self.path)
            n = int(self.headers.get("Content-Length") or 0)
            body = json.loads(self.rfile.read(n) or b"{}")
            try:
                fn = {"/api/commit": app.commit, "/api/produce": app.produce, "/api/activate": app.activate, "/api/mark": app.mark, "/api/resolution": app.resolution, "/api/evict": app.evict, "/api/prune": app.prune, "/api/say": app.say}.get(u.path)
                if fn is None:
                    self._json({"error": "not found"}, 404)
                    return
                self._json(fn(body))
            except Exception as exc:
                self._json({"error": f"{type(exc).__name__}: {exc}"}, 400)

        def log_message(self, fmt: str, *args: Any) -> None:  # quiet
            return

    return Handler


def build(store_dir: Optional[str], demo: bool):
    root = Path(store_dir) if store_dir else Path(tempfile.mkdtemp(prefix="nodecules-inspector-"))
    store = Store()
    tracker = Tracker(sink=JsonlSink(root / "events.jsonl"))
    tracker.attach(store)
    store.attach(DiskBacking(root))
    if demo and not store.scopes():
        seed_demo(store)
    generator = Generator(store, demo_inventory() if demo else [], tracker=tracker)
    return App(store, tracker, generator, root)


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--store", help="directory of a disk-backed store (created if missing)")
    ap.add_argument("--demo", action="store_true", help="seed a demo graph and load the demo inventory")
    ap.add_argument("--port", type=int, default=8765)
    args = ap.parse_args()
    app = build(args.store, args.demo)
    print(f"inspector on http://127.0.0.1:{args.port}/  store at {app.root}", flush=True)
    ThreadingHTTPServer(("127.0.0.1", args.port), make_handler(app)).serve_forever()


if __name__ == "__main__":
    main()
