#!/usr/bin/env python3
"""Serve a store directory, or pull one from a peer (`core/transport.py`).

    # on the machine that has the store (loopback only; no auth on the wire)
    PYTHONPATH=backend python3 handoff/transport.py serve --store handoff/stores/exchange-2 --port 7801

    # on the machine that wants it, through `ssh -L 7801:127.0.0.1:7801 <host>`
    PYTHONPATH=backend python3 handoff/transport.py pull --store <dir> --peer http://127.0.0.1:7801 --by mbp

    serve --until-stdin-closes   exit when stdin reaches EOF, so a server started
                                 as `ssh <host> ... serve --until-stdin-closes`
                                 lives exactly as long as the ssh connection
    pull --loop SECS             pull again every SECS seconds
    pull --scope S               only these scopes (repeatable)

`pull` prints one line per round: what it moved, what waits for a retry,
requests, bytes, and wall time. Nothing is pushed anywhere: the other side
pulls from your server when it wants what you have.
"""

from __future__ import annotations

import argparse
import sys
import threading
import time
from pathlib import Path

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE.parent / "backend"))

from nodecules.core.disk import DiskBacking, load  # noqa: E402
from nodecules.core.transport import Peer, pull, serve  # noqa: E402


def cmd_serve(a: argparse.Namespace) -> int:
    served = serve(DiskBacking(a.store), host=a.host, port=a.port)
    print(f"serving {a.store} at {served.url}", flush=True)
    stop = threading.Event()
    if a.until_stdin_closes:
        def watch() -> None:
            while sys.stdin.read(4096):
                pass
            stop.set()

        threading.Thread(target=watch, daemon=True).start()
    try:
        stop.wait()
    except KeyboardInterrupt:
        pass
    served.close()
    return 0


def pull_once(a: argparse.Namespace, peer: Peer) -> int:
    t0 = time.monotonic()
    report = pull(load(a.store), peer, scopes=a.scope, author=a.by)
    ms = (time.monotonic() - t0) * 1000
    waiting = sum(len(v) for v in report.retry.values())
    print(
        f"pulled from {peer.url}: {report.manifests} manifests, {report.bodies} bodies, "
        f"retry {waiting}, {report.requests} requests, {report.bytes} bytes, {ms:.1f} ms",
        flush=True,
    )
    for scope, heads in sorted(report.retry.items()):
        print(f"  retry {scope}: {', '.join(h[:12] for h in heads)} (not all of it is served yet)", flush=True)
    return 0


def cmd_pull(a: argparse.Namespace) -> int:
    peer = Peer(a.peer, timeout=a.timeout)
    while True:
        pull_once(a, peer)
        if not a.loop:
            return 0
        time.sleep(a.loop)


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    sub = ap.add_subparsers(dest="cmd", required=True)
    s = sub.add_parser("serve")
    s.add_argument("--store", required=True)
    s.add_argument("--host", default="127.0.0.1", help="bind address (default loopback; anything else is reachable without auth)")
    s.add_argument("--port", type=int, default=0, help="0 picks a free port")
    s.add_argument("--until-stdin-closes", action="store_true")
    s.set_defaults(fn=cmd_serve)
    p = sub.add_parser("pull")
    p.add_argument("--store", required=True)
    p.add_argument("--peer", required=True, help="http://host:port of a transport server")
    p.add_argument("--scope", action="append")
    p.add_argument("--by", required=True, help="who is pulling: the author of any merge")
    p.add_argument("--timeout", type=float, default=30.0)
    p.add_argument("--loop", type=float, default=0.0, metavar="SECS")
    p.set_defaults(fn=cmd_pull)
    a = ap.parse_args()
    return a.fn(a)


if __name__ == "__main__":
    sys.exit(main())
