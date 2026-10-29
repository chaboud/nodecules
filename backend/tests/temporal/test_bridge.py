"""The bridge (`handoff/bridge.py`): a pull loop over folders of stores,
with an optional git commit of what the wire brought.

Two roots on loopback stand in for two machines, and a bare repository for
GitHub. The cases are the ones the Air and the Spark meet: a store both
sides hold, a store only the peer holds, a peer that is down, git commits
made elsewhere at the same time, and a checkout that is not the bridge's
to commit."""

from __future__ import annotations

import importlib.util
import json
import os
import shutil
import socket
import subprocess
import sys
import time
from pathlib import Path

import pytest

from nodecules.core.disk import DiskBacking, load
from nodecules.core.store import Node
from nodecules.core.transport import Peer, serve_root

HANDOFF = Path(__file__).resolve().parents[3] / "handoff"
_spec = importlib.util.spec_from_file_location("bridge", HANDOFF / "bridge.py")
bridge = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(bridge)

S = "chat/tools"
BRANCH = "shared"


def _commit(store_dir: Path, name: str, data, author: str = "kid") -> str:
    store = load(store_dir)
    tx = store.transaction(S, author=author)
    tx.put(Node(id=name, kind="k", scope=S, data=data))
    return tx.commit().content_hash()


def _head(store_dir: Path) -> str:
    return load(store_dir).current(S).content_hash()


def _has(store_dir: Path, name: str) -> bool:
    store = load(store_dir)
    return name in dict(store.current(S).entries())


@pytest.fixture
def roots():
    running = []

    def start(root: Path):
        root.mkdir(parents=True, exist_ok=True)
        s = serve_root(root)
        running.append(s)
        return s

    yield start
    for s in running:
        s.close()


class Log(list):
    def __call__(self, line: str) -> None:
        self.append(line)


# --- pulling ----------------------------------------------------------------------------


def test_a_round_pulls_the_stores_both_sides_hold_and_leaves_the_rest(tmp_path, roots):
    spark, air = tmp_path / "spark", tmp_path / "air"
    _commit(spark / "ex-1", "answer", {"v": 1})
    _commit(spark / "scratch", "mine", {"v": 2})
    served = roots(spark)
    DiskBacking(air / "ex-1")  # the Air holds ex-1, empty, and not scratch
    log = Log()

    bridge.Bridge(air, [served.url], by="bridge", log=log).round()

    assert _head(air / "ex-1") == _head(spark / "ex-1")
    assert not (air / "scratch").exists()
    assert any("ex-1" in line and "1 manifests" in line for line in log), log


def test_new_takes_every_store_the_peer_lists(tmp_path, roots):
    air, spark = tmp_path / "air", tmp_path / "spark"
    _commit(air / "ex-1", "request", {"q": 1})
    _commit(air / "ex-2", "request", {"q": 2})
    served = roots(air)

    bridge.Bridge(spark, [served.url], by="bridge", new=True, log=Log()).round()

    assert _head(spark / "ex-1") == _head(air / "ex-1")
    assert _head(spark / "ex-2") == _head(air / "ex-2")


def test_what_a_pull_moved_is_in_the_events_log_of_by(tmp_path, roots):
    spark, air = tmp_path / "spark", tmp_path / "air"
    _commit(spark / "ex-1", "answer", {"v": 1})
    served = roots(spark)
    DiskBacking(air / "ex-1")

    bridge.Bridge(air, [served.url], by="bridge", log=Log()).round()

    events = [json.loads(line) for line in (air / "ex-1" / "events-bridge.jsonl").read_text().splitlines()]
    assert any(e["detail"].get("manifest") == _head(spark / "ex-1") for e in events), events
    assert not (spark / "ex-1" / "events-bridge.jsonl").exists()


def test_a_round_with_nothing_new_says_nothing(tmp_path, roots):
    spark, air = tmp_path / "spark", tmp_path / "air"
    _commit(spark / "ex-1", "answer", {"v": 1})
    served = roots(spark)
    DiskBacking(air / "ex-1")
    b = bridge.Bridge(air, [served.url], by="bridge", log=Log())
    b.round()
    b.log = log = Log()
    b.round()
    assert log == []


def _free_port() -> int:
    with socket.socket() as s:
        s.bind(("127.0.0.1", 0))
        return s.getsockname()[1]


def test_a_peer_that_is_down_is_said_once_and_its_return_is_said_once(tmp_path):
    spark, air = tmp_path / "spark", tmp_path / "air"
    _commit(spark / "ex-1", "answer", {"v": 1})
    DiskBacking(air / "ex-1")
    port = _free_port()
    log = Log()
    b = bridge.Bridge(air, [f"http://127.0.0.1:{port}"], by="bridge", log=log, timeout=2)

    b.round()
    b.round()
    assert len([line for line in log if "unreachable" in line]) == 1, log

    served = serve_root(spark, port=port)
    try:
        b.round()
    finally:
        served.close()
    assert any("reachable again" in line for line in log), log
    assert _head(air / "ex-1") == _head(spark / "ex-1")


# --- git --------------------------------------------------------------------------------



def _env(tmp: Path) -> dict:
    who = {"GIT_AUTHOR_NAME": "t", "GIT_AUTHOR_EMAIL": "t@example.invalid", "GIT_COMMITTER_NAME": "t", "GIT_COMMITTER_EMAIL": "t@example.invalid"}
    return {**os.environ, **who, "HOME": str(tmp), "GIT_CONFIG_NOSYSTEM": "1"}


class Git:
    """GitHub (a bare repo), the Air's bridge clone, and cloud's clone, each
    with the exchange store `handoff/stores/ex-1` committed."""

    def __init__(self, tmp: Path, monkeypatch) -> None:
        self.env = _env(tmp)
        for k in ("GIT_AUTHOR_NAME", "GIT_AUTHOR_EMAIL", "GIT_COMMITTER_NAME", "GIT_COMMITTER_EMAIL", "HOME", "GIT_CONFIG_NOSYSTEM"):
            monkeypatch.setenv(k, self.env[k])
        self.origin, self.air, self.cloud = tmp / "origin.git", tmp / "air", tmp / "cloud"
        self.run(tmp, "init", "-q", "--bare", "-b", BRANCH, str(self.origin))
        self.run(tmp, "clone", "-q", str(self.origin), str(self.cloud))
        self.run(self.cloud, "checkout", "-q", "-b", BRANCH)
        (self.cloud / "README").write_text("one\n")
        _commit(self.cloud / "handoff" / "stores" / "ex-1", "request", {"q": 1}, author="cloud")
        self.run(self.cloud, "add", "-A")
        self.run(self.cloud, "commit", "-q", "-m", "seed ex-1")
        self.run(self.cloud, "push", "-q", "origin", f"HEAD:{BRANCH}")
        self.run(tmp, "clone", "-q", "-b", BRANCH, str(self.origin), str(self.air))
        self.stores = self.air / "handoff" / "stores"

    def run(self, cwd: Path, *args: str, check: bool = True) -> str:
        p = subprocess.run(["git", *args], cwd=cwd, env=self.env, text=True, capture_output=True, timeout=60)
        if check and p.returncode != 0:
            raise AssertionError(f"git {' '.join(args)} in {cwd.name}: {p.stderr}")
        return p.stdout.strip()

    def origin_subjects(self) -> list:
        return self.run(self.origin, "log", "--format=%s", BRANCH).splitlines()

    def origin_files(self, rev: str = BRANCH) -> list:
        return self.run(self.origin, "show", "--name-only", "--format=", rev).splitlines()

    def cloud_commits(self, name: str) -> str:
        self.run(self.cloud, "pull", "-q", "--rebase")
        h = _commit(self.cloud / "handoff" / "stores" / "ex-1", name, {"q": name}, author="cloud")
        self.run(self.cloud, "add", "-A")
        self.run(self.cloud, "commit", "-q", "-m", f"cloud: {name}")
        self.run(self.cloud, "push", "-q")
        return h


@pytest.fixture
def g(tmp_path, monkeypatch):
    if shutil.which("git") is None:
        pytest.skip("needs git")
    return Git(tmp_path, monkeypatch)


def _spark_answers(tmp_path: Path, g: Git, roots) -> tuple:
    """The Spark's root: ex-1 as the Air has it, plus one answer."""
    spark = tmp_path / "spark"
    air_served = roots(g.stores)
    bridge.Bridge(spark, [air_served.url], by="bridge", new=True, log=Log()).round()
    answer = _commit(spark / "ex-1", "answer", {"a": 1}, author="spark")
    return spark, roots(spark), answer


def test_git_commits_what_the_wire_brought_as_by_and_pushes_it(tmp_path, g, roots):
    spark, served, answer = _spark_answers(tmp_path, g, roots)
    log = Log()

    bridge.Bridge(g.stores, [served.url], by="bridge", git=True, log=log).round()

    assert g.origin_subjects()[0].startswith("bridge: "), g.origin_subjects()
    files = g.origin_files()
    assert files and all(f.startswith("handoff/stores/ex-1/") for f in files), files
    assert "handoff/stores/ex-1/events-bridge.jsonl" in files
    assert g.run(g.origin, "rev-parse", BRANCH) == g.run(g.air, "rev-parse", "HEAD")
    assert any("pushed" in line for line in log), log


def test_git_takes_what_others_pushed_so_the_peer_can_pull_it(tmp_path, g, roots):
    air_served = roots(g.stores)
    later = g.cloud_commits("second request")

    bridge.Bridge(g.stores, [], by="bridge", git=True, log=Log()).round()

    assert _head(g.stores / "ex-1") == later
    spark = tmp_path / "spark"
    bridge.Bridge(spark, [air_served.url], by="bridge", new=True, log=Log()).round()
    assert _head(spark / "ex-1") == later


def test_git_edits_on_both_sides_at_once_converge_without_a_conflict(tmp_path, g, roots):
    spark, served, answer = _spark_answers(tmp_path, g, roots)
    g.cloud_commits("a request made meanwhile")
    log = Log()

    bridge.Bridge(g.stores, [served.url], by="bridge", git=True, log=log).round()

    assert g.run(g.origin, "rev-parse", BRANCH) == g.run(g.air, "rev-parse", "HEAD"), log
    assert g.run(g.air, "status", "--porcelain") == ""
    assert _has(g.stores / "ex-1", "answer") and _has(g.stores / "ex-1", "a request made meanwhile")


def test_git_refuses_a_protected_branch(tmp_path, g):
    g.run(g.air, "checkout", "-q", "-b", "main")
    g.run(g.air, "push", "-q", "-u", "origin", "main")  # an upstream, so only the branch's name can refuse it
    with pytest.raises(ValueError, match="on main; the bridge never commits there"):
        bridge.Bridge(g.stores, [], by="bridge", git=True, log=Log())


def test_git_leaves_a_checkout_with_other_edits_alone(tmp_path, g, roots):
    spark, served, answer = _spark_answers(tmp_path, g, roots)
    (g.air / "README").write_text("someone is editing this\n")
    before = g.origin_subjects()
    log = Log()

    bridge.Bridge(g.stores, [served.url], by="bridge", git=True, log=log).round()

    assert g.origin_subjects() == before
    assert (g.air / "README").read_text() == "someone is editing this\n"
    assert any("README" in line for line in log), log


# --- the held command, and the command line --------------------------------------------


def test_a_held_command_is_restarted_when_it_exits_and_its_output_is_logged():
    log = Log()
    hold = bridge.Hold(f"{sys.executable} -c \"print('up', flush=True)\"", log=log, backoff=0.05)
    hold.start()
    try:
        deadline = time.monotonic() + 10
        while len([line for line in log if line.endswith("up")]) < 2 and time.monotonic() < deadline:
            hold.check()
            time.sleep(0.05)
    finally:
        hold.stop()
    assert len([line for line in log if line.endswith("up")]) >= 2, log


def test_stopping_a_held_command_closes_its_stdin_first():
    log = Log()
    code = "import sys; print('up', flush=True); sys.stdin.read(); print('stdin closed', flush=True)"
    hold = bridge.Hold(f"{sys.executable} -c \"{code}\"", log=log, backoff=0.05)
    hold.start()
    deadline = time.monotonic() + 10
    while not any(line.endswith("up") for line in log) and time.monotonic() < deadline:
        time.sleep(0.05)
    hold.stop()
    assert any(line.endswith("stdin closed") for line in log), log


def test_run_serves_its_root_and_exits_when_stdin_closes(tmp_path):
    _commit(tmp_path / "root" / "ex-1", "answer", {"v": 1})
    port = _free_port()
    env = {**os.environ, "PYTHONPATH": str(HANDOFF.parent / "backend")}
    p = subprocess.Popen(
        [sys.executable, str(HANDOFF / "bridge.py"), "run", "--root", str(tmp_path / "root"), "--serve-port", str(port), "--by", "t", "--every", "0.2", "--until-stdin-closes"],
        stdin=subprocess.PIPE, stdout=subprocess.PIPE, stderr=subprocess.STDOUT, text=True, env=env,
    )
    try:
        deadline = time.monotonic() + 15
        listed = None
        while listed is None and time.monotonic() < deadline:
            try:
                listed = Peer(f"http://127.0.0.1:{port}", timeout=1).stores()
            except OSError:
                time.sleep(0.1)
        assert listed == ["ex-1"]
        p.stdin.close()
        assert p.wait(timeout=10) == 0
    finally:
        if p.poll() is None:
            p.kill()
