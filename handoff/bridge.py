#!/usr/bin/env python3
"""The bridge: a pull loop over folders of stores (vault ADR-0035, note 0024).

Each machine runs one. It serves its folder of stores on loopback, pulls
from its peers every so often, and, where the folder is in a git checkout,
commits what the wire brought and pushes it, so a party that can reach
stores only through git (cloud) sees them:

    # the Air: serve the git checkout's stores, pull the Spark's, commit as `bridge`
    PYTHONPATH=backend python3 handoff/bridge.py run --root handoff/stores \\
        --serve-port 17802 --peer http://127.0.0.1:17801 --by bridge --every 300 --git \\
        --hold "ssh -L 17801:127.0.0.1:17801 -R 17802:127.0.0.1:17802 <host> '<the Spark side>'"

    # the Spark side, started by that ssh and living as long as it does
    PYTHONPATH=backend python3 handoff/bridge.py run --root ~/nodecules-stores \\
        --serve-port 17801 --peer http://127.0.0.1:17802 --new --by bridge --every 30 \\
        --until-stdin-closes

A peer is a root of stores (`transport.serve_root`), and a round pulls every
store the peer lists that this side also holds. `--new` also takes the ones
it does not hold yet, so a new exchange reaches the other side without
anyone configuring it. Without `--new`, a store only the peer holds stays
there: the git side never commits a peer's scratch store.

What a round moved goes to the log, one line per store, in the form
`transport.py pull` prints; a round that moved nothing says nothing, and a
peer that goes down is said once, and again when it comes back. Head moves
go to `<store>/events-<by>.jsonl`.

With `--git`, after the pulls: everything under the root is committed as
`<by>: ...`, then `git pull --rebase` takes what others pushed, then the
commits are pushed. A store directory merges in git without conflicts
(heads are candidate files, events logs are per party), so the rebase does
not stop. It refuses main, master, and feat/temporality, and leaves the
checkout alone while anything outside the root is uncommitted.

`--hold CMD` keeps a command running beside the loop, restarting it when it
exits (5 s, doubling to 5 min; reset after it has run a minute), with its
output in the log. Stopping the bridge closes the command's stdin first,
so an ssh session running the other side's bridge with
`--until-stdin-closes` ends it.
"""

from __future__ import annotations

import argparse
import re
import signal
import subprocess
import sys
import threading
import time
from http.client import HTTPException
from pathlib import Path
from typing import Callable, Dict, List, Optional, Tuple

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE.parent / "backend"))

from nodecules.core.disk import Corrupt, load  # noqa: E402
from nodecules.core.tracking import JsonlSink, Tracker  # noqa: E402
from nodecules.core.transport import STORE_NAME, Peer, TransportError, pull, serve_root  # noqa: E402

PROTECTED = ("main", "master", "feat/temporality")
PARTY = re.compile(r"^[a-z][a-z0-9-]*$")


def _git(repo: Path, *args: str) -> subprocess.CompletedProcess:
    return subprocess.run(["git", "-C", str(repo), *args], text=True, capture_output=True, timeout=120)


def _last(text: str) -> str:
    lines = [line for line in text.strip().splitlines() if line.strip()]
    return lines[-1] if lines else ""


class Bridge:
    """One side of the bridge: a root of stores, the peers it pulls from,
    and, with `git`, the checkout the root is in."""

    def __init__(
        self,
        root: str | Path,
        peers: List[str],
        *,
        by: str,
        new: bool = False,
        git: bool = False,
        log: Callable[[str], None] = print,
        timeout: float = 30.0,
    ) -> None:
        if not PARTY.match(by):
            raise ValueError(f"--by names a party (lowercase letters, digits, dashes), got {by!r}")
        self.root = Path(root).expanduser()
        self.peers = [p.rstrip("/") for p in peers]
        self.by, self.new, self.git, self.log, self.timeout = by, new, git, log, timeout
        self._roots: Dict[str, Peer] = {}
        self._stores: Dict[Tuple[str, str], Peer] = {}
        self._down: Dict[str, bool] = {}
        self._moved: Dict[str, List[int]] = {}  # store -> [manifests, bodies] since the last commit
        self._blocked = ""
        self.repo: Optional[Path] = None
        if git:
            top = _git(self.root, "rev-parse", "--show-toplevel")
            if top.returncode != 0:
                raise ValueError(f"--git: {self.root} is not in a git checkout")
            self.repo = Path(top.stdout.strip())
            branch = _git(self.repo, "rev-parse", "--abbrev-ref", "HEAD").stdout.strip()
            if branch in PROTECTED:
                raise ValueError(f"--git: the checkout is on {branch}; the bridge never commits there")
            if _git(self.repo, "rev-parse", "--abbrev-ref", "@{u}").returncode != 0:
                raise ValueError(f"--git: {branch} has no upstream to push to")
            self.rel = str(self.root.resolve().relative_to(self.repo.resolve()))

    # -- one round -----------------------------------------------------------------------

    def round(self) -> None:
        for url in self.peers:
            self._pull_peer(url)
        if self.git:
            self._git_round()

    def _pull_peer(self, url: str) -> None:
        listing = self._roots.setdefault(url, Peer(url, timeout=self.timeout))
        try:
            names = listing.stores()
        except (OSError, HTTPException) as e:
            if not self._down.get(url):
                self.log(f"peer {url} unreachable: {e}")
            self._down[url] = True
            return
        except TransportError as e:
            self.log(f"peer {url}: {e}")
            return
        if self._down.get(url):
            self.log(f"peer {url} reachable again")
        self._down[url] = False
        if names is None:
            self.log(f"peer {url} serves one store, not a root of stores; skipped")
            return
        for name in names:
            if not STORE_NAME.match(name):
                continue
            if not (self.root / name / "manifests").is_dir() and not self.new:
                continue
            self._pull_store(url, name)

    def _pull_store(self, url: str, name: str) -> None:
        peer = self._stores.setdefault((url, name), Peer(f"{url}/{name}", timeout=self.timeout))
        store_dir = self.root / name
        t0 = time.monotonic()
        try:
            store = load(store_dir)
            tracker = Tracker()
            tracker.attach(store)
            tracker.add_sink(JsonlSink(store_dir / f"events-{self.by}.jsonl"))
            report = pull(store, peer, author=self.by)
        except Corrupt as e:
            self.log(f"refused {name} from {url}: {e}")
            return
        except (OSError, HTTPException, TransportError, ValueError) as e:
            self.log(f"pull of {name} from {url} failed, next round tries again: {e!r}")
            return
        waiting = sum(len(v) for v in report.retry.values())
        if not (report.manifests or report.bodies or waiting):
            return
        moved = self._moved.setdefault(name, [0, 0])
        moved[0] += report.manifests
        moved[1] += report.bodies
        ms = (time.monotonic() - t0) * 1000
        self.log(
            f"pulled {name} from {url}: {report.manifests} manifests, {report.bodies} bodies, "
            f"retry {waiting}, {report.requests} requests, {report.bytes} bytes, {ms:.1f} ms"
        )

    # -- git -----------------------------------------------------------------------------

    def _say_blocked(self, why: str) -> None:
        if why != self._blocked and why:
            self.log(f"git: {why}")
        self._blocked = why

    def _git_round(self) -> None:
        repo = self.repo
        assert repo is not None
        status = _git(repo, "status", "--porcelain", "--untracked-files=all")
        prefix = self.rel.rstrip("/") + "/"
        outside = [line[3:] for line in status.stdout.splitlines() if not line[3:].startswith(prefix)]
        if outside:
            self._say_blocked(f"left alone: uncommitted edits outside {self.rel}: {', '.join(outside[:5])}")
            return
        self._say_blocked("")

        _git(repo, "add", "-A", "--", self.rel)
        if _git(repo, "diff", "--cached", "--quiet").returncode != 0:
            changed = sorted({p[len(prefix):].split("/", 1)[0] for p in _git(repo, "diff", "--cached", "--name-only").stdout.split()})
            parts = []
            for name in changed:
                m, b = self._moved.get(name, [0, 0])
                parts.append(f"{name} ({m} manifests, {b} bodies over the wire)" if m or b else name)
            subject = f"{self.by}: {', '.join(parts)}"
            done = _git(repo, "commit", "-q", "-m", subject)
            if done.returncode != 0:
                self.log(f"git: commit failed: {_last(done.stderr or done.stdout)}")
                return
            self._moved.clear()
            self.log(f"git: committed {_git(repo, 'rev-parse', '--short', 'HEAD').stdout.strip()} {subject}")

        before = _git(repo, "rev-parse", "@{u}").stdout.strip()
        took = _git(repo, "pull", "--rebase", "-q")
        if took.returncode != 0:
            if (repo / ".git" / "rebase-merge").exists() or (repo / ".git" / "rebase-apply").exists():
                _git(repo, "rebase", "--abort")
            self.log(f"git: pull --rebase failed, kept the commits, next round tries again: {_last(took.stderr)}")
            return
        after = _git(repo, "rev-parse", "@{u}").stdout.strip()
        if after != before:
            n = _git(repo, "rev-list", "--count", f"{before}..{after}").stdout.strip()
            self.log(f"git: took {n} commit(s) from upstream")

        ahead = int(_git(repo, "rev-list", "--count", "@{u}..HEAD").stdout.strip() or 0)
        if ahead:
            pushed = _git(repo, "push", "-q")
            if pushed.returncode != 0:
                self.log(f"git: push failed, next round tries again: {_last(pushed.stderr)}")
                return
            self.log(f"git: pushed {ahead} commit(s)")


class Hold:
    """A command kept running beside the loop, its output in the log."""

    def __init__(self, cmd: str, *, log: Callable[[str], None] = print, backoff: float = 5.0, max_backoff: float = 300.0) -> None:
        self.cmd, self.log = cmd, log
        self.base, self.max = backoff, max_backoff
        self.backoff = backoff
        self.proc: Optional[subprocess.Popen] = None
        self._reader: Optional[threading.Thread] = None
        self._since = 0.0
        self._next: Optional[float] = None

    def start(self) -> None:
        self.proc = subprocess.Popen(
            self.cmd, shell=True, stdin=subprocess.PIPE, stdout=subprocess.PIPE, stderr=subprocess.STDOUT,
            text=True, bufsize=1, start_new_session=True,
        )
        self._since = time.monotonic()
        self._next = None
        out = self.proc.stdout

        def read() -> None:
            for line in out:
                self.log(f"hold| {line.rstrip()}")

        self._reader = threading.Thread(target=read, name="hold-reader", daemon=True)
        self._reader.start()

    def check(self) -> None:
        """Start the command again if it has exited and its wait is over."""
        if self.proc is None or self.proc.poll() is None:
            return
        now = time.monotonic()
        if self._next is None:
            if now - self._since > 60:
                self.backoff = self.base
            self.log(f"held command exited (rc {self.proc.returncode}); starting it again in {self.backoff:g} s")
            self._next = now + self.backoff
            self.backoff = min(self.backoff * 2, self.max)
            return
        if now >= self._next:
            self.start()

    def stop(self) -> None:
        p = self.proc
        if p is None:
            return
        try:
            if p.stdin:
                p.stdin.close()
            p.wait(timeout=5)
        except (subprocess.TimeoutExpired, OSError):
            p.terminate()
            try:
                p.wait(timeout=5)
            except subprocess.TimeoutExpired:
                p.kill()
                p.wait()
        if self._reader is not None:
            self._reader.join(timeout=5)


def _stamp(line: str) -> None:
    print(f"{time.strftime('%Y-%m-%d %H:%M:%S %Z')} {line}", flush=True)


def cmd_run(a: argparse.Namespace) -> int:
    root = Path(a.root).expanduser()
    root.mkdir(parents=True, exist_ok=True)
    try:
        b = Bridge(root, a.peer or [], by=a.by, new=a.new, git=a.git, log=_stamp, timeout=a.timeout)
    except ValueError as e:
        print(f"bridge: {e}", file=sys.stderr)
        return 2
    if a.once:
        b.round()
        return 0
    stop = threading.Event()
    for sig in (signal.SIGTERM, signal.SIGINT):
        signal.signal(sig, lambda *_: stop.set())
    if a.until_stdin_closes:
        def watch() -> None:
            while sys.stdin.read(4096):
                pass
            stop.set()

        threading.Thread(target=watch, name="stdin-watch", daemon=True).start()
    served = serve_root(root, port=a.serve_port) if a.serve_port else None
    hold = Hold(a.hold, log=_stamp) if a.hold else None
    _stamp(
        f"bridge up as {a.by}: root {root}"
        + (f", serving {served.url}" if served else "")
        + (f", pulling {', '.join(b.peers)}" if b.peers else "")
        + f", every {a.every:g} s" + (", git" if a.git else "") + (", new stores too" if a.new else "")
    )
    if hold:
        hold.start()
    try:
        while not stop.is_set():
            try:
                b.round()
            except Exception as e:  # one bad round never ends the loop
                _stamp(f"round failed: {e!r}")
            end = time.monotonic() + a.every
            while not stop.is_set() and time.monotonic() < end:
                stop.wait(min(1.0, max(0.0, end - time.monotonic())))
                if hold:
                    hold.check()
    finally:
        if hold:
            hold.stop()
        if served:
            served.close()
        _stamp("bridge down")
    return 0


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    sub = ap.add_subparsers(dest="cmd", required=True)
    r = sub.add_parser("run")
    r.add_argument("--root", required=True, help="the folder of stores this side serves and pulls into")
    r.add_argument("--peer", action="append", help="http://host:port of the other side's root (repeatable)")
    r.add_argument("--by", required=True, help="the party this bridge commits and merges as")
    r.add_argument("--serve-port", type=int, default=0, help="serve the root on this loopback port (default: do not serve)")
    r.add_argument("--every", type=float, default=60.0, metavar="SECS")
    r.add_argument("--new", action="store_true", help="also take stores the peer has and this side does not")
    r.add_argument("--git", action="store_true", help="commit the root as --by, pull --rebase, push, every round")
    r.add_argument("--hold", metavar="CMD", help="keep this shell command running beside the loop")
    r.add_argument("--until-stdin-closes", action="store_true")
    r.add_argument("--once", action="store_true", help="one round, then exit")
    r.add_argument("--timeout", type=float, default=30.0)
    r.set_defaults(fn=cmd_run)
    a = ap.parse_args()
    return a.fn(a)


if __name__ == "__main__":
    sys.exit(main())
