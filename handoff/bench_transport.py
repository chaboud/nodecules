#!/usr/bin/env python3
"""Measure the replica transport against the git relay, between the keyed
box (where this runs) and the Spark, over the LAN.

    cd backend && PYTHONPATH=. python3 ../handoff/bench_transport.py --reps 10 \\
        --out ../handoff/results/<date>-transport-vs-relay.md

Everything happens in scratch space; the Spark's own checkout is never
touched. On the Spark: a clone of the shared branch under --remote-work
(over HTTPS; nodecules is public), run with the Spark's existing venv
python. On this box: a temp directory. This file is uploaded to the Spark
each run, so both ends run the same code.

What is timed:

- **Transport**, one ssh connection carrying both tunnels (`-L` for the
  Spark's server, `-R` for this box's), as a runner would hold it:
  a fulfilment-sized commit (one manifest, three bodies, about 3.5 KB) on
  one side, then one pull on the other, timed inside the pull, both ways;
  a no-op pull both ways (the floor of a polling loop); a cold pull of
  exchange-2; cold, incremental, and no-op pulls of two synthetic stores
  (wide: many scopes, shallow; deep: one scope, long history).
- **Git relay**, on a scratch branch deleted afterwards, as the runner
  calls it (a fresh ssh connection per step, no multiplexing): the Spark
  commits, then `spark-relay.sh up` (fetch from the Spark, rebase on
  GitHub, push); this box commits, then `git push` and `spark-relay.sh
  down`; and `git push` then the Spark pulling from GitHub itself; a
  no-op `up`.

Endpoints come from flags or the environment (SPARK), never from the
script. Orchestration commands use a multiplexed ssh connection; it is not
part of any timed step except where a step is itself an ssh round trip
(the relay's), which is said in the table.
"""

from __future__ import annotations

import argparse
import json
import os
import platform
import shlex
import shutil
import statistics
import subprocess
import sys
import tempfile
import time
from pathlib import Path
from typing import Dict, List, Optional

HERE = Path(__file__).resolve().parent
sys.path.insert(0, os.environ.get("NB_BACKEND", str(HERE.parent / "backend")))

from nodecules.core.disk import load  # noqa: E402
from nodecules.core.store import Node  # noqa: E402
from nodecules.core.transport import Peer, pull  # noqa: E402

SHARED = "claude/nodecules-v2-naming-matching-vmkexv"


# --- helpers that run on either machine -------------------------------------------------


def commit_fulfilment(store_dir: str, scope: str, author: str, i: int) -> None:
    """One commit shaped like a fulfilment: an answer, its envelope, a note."""
    s = load(store_dir)
    tx = s.transaction(scope, author=author)
    # Content addressing dedupes equal data across scopes and machines, so every body names its scope.
    tx.put(Node(id=f"answer/{i}", kind="bench.answer", scope=scope, data={"i": i, "at": scope, "content": "a" * 2000}))
    tx.put(Node(id=f"envelope/{i}", kind="bench.envelope", scope=scope, data={"i": i, "at": scope, "recipe": {"by": author}, "pad": "e" * 900}))
    tx.put(Node(id=f"note/{i}", kind="bench.note", scope=scope, data={"i": i, "at": scope}))
    tx.commit(f"bench {i}")


def synth(store_dir: str, scopes: int, commits: int, nodes: int) -> None:
    s = load(store_dir)
    for c in range(commits):
        for k in range(scopes):
            scope = f"synth/{k:03d}"
            tx = s.transaction(scope, author="synth")
            for n in range(nodes):
                tx.put(Node(id=f"n{c}-{n}", kind="bench.synth", scope=scope, data={"at": scope, "c": c, "n": n, "pad": "s" * 200}))
            tx.commit(f"synth {c}")


def timed_pull(store_dir: str, url: str, author: str) -> dict:
    peer = Peer(url)
    store = load(store_dir)  # loading is not the wire; it is timed apart if at all
    t0 = time.perf_counter()
    r = pull(store, peer, author=author)
    ms = (time.perf_counter() - t0) * 1000
    peer.close()
    return {"ms": ms, "manifests": r.manifests, "bodies": r.bodies, "requests": r.requests, "bytes": r.bytes, "retry": sum(len(v) for v in r.retry.values())}


def timed_cmd(argv: List[str], cwd: Optional[str] = None, env: Optional[dict] = None) -> dict:
    t0 = time.perf_counter()
    p = subprocess.run(argv, cwd=cwd, env=env, text=True, capture_output=True, timeout=600)
    ms = (time.perf_counter() - t0) * 1000
    if p.returncode != 0:
        raise RuntimeError(f"{' '.join(argv)} -> {p.returncode}: {p.stderr.strip()}")
    return {"ms": ms}


def push(repo: str, branch: str, env: dict, failures: List[str]) -> float:
    """Push to GitHub, timed; one retry, and every failure is kept, because a
    failed push is part of what the relay costs."""
    t0 = time.perf_counter()
    for attempt in (0, 1):
        p = subprocess.run(["git", "-C", repo, "push", "-q", "origin", branch], env=env, text=True, capture_output=True, timeout=120)
        if p.returncode == 0:
            return (time.perf_counter() - t0) * 1000
        failures.append(f"push exit {p.returncode}: {p.stderr.strip().splitlines()[-1] if p.stderr.strip() else ''}")
    raise RuntimeError(f"push failed twice: {failures[-2:]}")


def helper(argv: List[str]) -> int:
    cmd, rest = argv[0], argv[1:]
    if cmd == "commit":
        commit_fulfilment(rest[0], rest[1], rest[2], int(rest[3]))
    elif cmd == "synth":
        synth(rest[0], int(rest[1]), int(rest[2]), int(rest[3]))
    elif cmd == "pull":
        print(json.dumps(timed_pull(rest[0], rest[1], rest[2])))
    elif cmd == "timed":
        print(json.dumps(timed_cmd(rest)))
    else:
        raise SystemExit(f"unknown helper {cmd}")
    return 0


# --- orchestration on the keyed box ---------------------------------------------------------


class Spark:
    def __init__(self, host: str, work: str, python: str, ctl: str) -> None:
        self.host, self.work, self.python = host, work, python
        self.mux = ["-o", "BatchMode=yes", "-o", "ControlMaster=auto", "-o", f"ControlPath={ctl}", "-o", "ControlPersist=300"]

    @property
    def py(self) -> str:
        return f"NB_BACKEND={self.work}/nodecules/backend {self.python} {self.work}/bench_transport.py _helper"

    def sh(self, cmd: str, stdin: Optional[str] = None) -> str:
        p = subprocess.run(["ssh", *self.mux, self.host, cmd], input=stdin, text=True, capture_output=True, timeout=1800)
        if p.returncode != 0:
            raise RuntimeError(f"on the Spark: {cmd[:120]} -> {p.returncode}: {p.stderr.strip()}")
        return p.stdout

    def helper(self, *args: str) -> str:
        return self.sh(f"{self.py} " + " ".join(shlex.quote(a) for a in args))

    def popen(self, cmd: str) -> subprocess.Popen:
        return subprocess.Popen(["ssh", *self.mux, self.host, cmd], stdin=subprocess.PIPE, stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True)


def start_server(p: subprocess.Popen, what: str) -> str:
    line = p.stdout.readline()
    if not line.startswith("serving "):
        raise RuntimeError(f"{what} did not start: {line!r} {p.stderr.read() if p.poll() is not None else ''}")
    return line.split()[-1]


def stop(p: subprocess.Popen) -> None:
    try:
        p.stdin.close()
        p.wait(timeout=15)
    except Exception:
        p.kill()


def summarize(rows: List[dict]) -> dict:
    ms = [r["ms"] for r in rows]
    out = {"n": len(ms), "median": statistics.median(ms), "min": min(ms), "max": max(ms)}
    for k in ("manifests", "bodies", "requests", "bytes"):
        if k in rows[0]:
            out[k] = statistics.median(r[k] for r in rows)
    return out


def main() -> int:
    if len(sys.argv) > 1 and sys.argv[1] == "_helper":
        return helper(sys.argv[2:])
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--spark", default=os.environ.get("SPARK", "spark-b23f"))
    ap.add_argument("--remote-work", default="~/mbp-transport")
    ap.add_argument("--remote-python", default="~/git/nodecules/backend/.venv/bin/python")
    ap.add_argument("--remote-url", default="https://github.com/chaboud/nodecules.git")
    ap.add_argument("--branch", default=SHARED, help="what the Spark's scratch clone checks out")
    ap.add_argument("--bench-branch", default="claude/mbp-transport-bench")
    ap.add_argument("--ports", default="17801,17802", help="the Spark's server port, this box's server port")
    ap.add_argument("--reps", type=int, default=10)
    ap.add_argument("--big-reps", type=int, default=3)
    ap.add_argument("--skip-git", action="store_true")
    ap.add_argument("--skip-transport", action="store_true")
    ap.add_argument("--work", help="local scratch parent (default: beside the repo, on its filesystem)")
    ap.add_argument("--keep", action="store_true", help="leave the scratch directories in place")
    ap.add_argument("--out")
    a = ap.parse_args()
    p1, p2 = (int(x) for x in a.ports.split(","))
    py_local = sys.executable
    backend = str(HERE.parent / "backend")
    # Beside the repo, not in the system temp dir: the repo has paths that collide on a
    # case-insensitive filesystem (CLAUDE.md, known traps), so a clone there starts dirty.
    work = Path(tempfile.mkdtemp(prefix="nb-bench-", dir=a.work or HERE.parent.parent))
    ctl = f"/tmp/nbx-{os.getpid()}-%C"
    spark = Spark(a.spark, a.remote_work, a.remote_python, ctl)
    home = spark.sh('printf %s "$HOME"')
    a.remote_work = a.remote_work.replace("~", home, 1) if a.remote_work.startswith("~") else a.remote_work
    a.remote_python = a.remote_python.replace("~", home, 1) if a.remote_python.startswith("~") else a.remote_python
    spark.work, spark.python = a.remote_work, a.remote_python
    results: Dict[str, dict] = {}
    bench_pushed = False
    notes: List[str] = []

    def log(msg: str) -> None:
        print(f"{time.strftime('%H:%M:%S')} {msg}", flush=True)

    # -- setup ---------------------------------------------------------------------------
    log("setup: scratch clone on the Spark")
    spark.sh(f"rm -rf {a.remote_work} && mkdir -p {a.remote_work}/stores && git clone -q -b {a.branch} {a.remote_url} {a.remote_work}/nodecules")
    spark.sh(f"cat > {a.remote_work}/bench_transport.py", stdin=Path(__file__).read_text())
    head = spark.sh(f"git -C {a.remote_work}/nodecules rev-parse --short HEAD").strip()
    remote_pyver = spark.sh(f"{a.remote_python} -c 'import sys; print(sys.version.split()[0])'").strip()
    tunnel = subprocess.Popen(
        ["ssh", "-N", "-o", "BatchMode=yes", "-o", "ExitOnForwardFailure=yes", "-L", f"{p1}:127.0.0.1:{p1}", "-R", f"{p2}:127.0.0.1:{p2}", a.spark],
        stdout=subprocess.DEVNULL, stderr=subprocess.PIPE, text=True,
    )
    time.sleep(1.5)
    if tunnel.poll() is not None:
        raise RuntimeError(f"tunnel failed: {tunnel.stderr.read()}")

    try:
        if not a.skip_transport:
            # -- transport, small store, both directions ---------------------------------------
            log("transport: small store, both directions")
            r_store, l_store = f"{a.remote_work}/stores/small", str(work / "small")
            spark.helper("commit", r_store, "bench/spark", "spark", "0")
            commit_fulfilment(l_store, "bench/air", "mbp", 0)
            rs = spark.popen(f"NB_BACKEND={a.remote_work}/nodecules/backend {a.remote_python} {a.remote_work}/nodecules/handoff/transport.py serve --store {r_store} --port {p1} --until-stdin-closes")
            ls = subprocess.Popen([py_local, str(HERE / "transport.py"), "serve", "--store", l_store, "--port", str(p2), "--until-stdin-closes"], stdin=subprocess.PIPE, stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True, env={**os.environ, "PYTHONPATH": backend})
            r_url, l_url = start_server(rs, "the Spark's server"), start_server(ls, "this box's server")
            to_spark = f"http://127.0.0.1:{p1}"   # the Spark's server, through -L
            to_air = f"http://127.0.0.1:{p2}"     # this box's server, from the Spark through -R
            timed_pull(l_store, to_spark, "mbp")  # first contact both ways, so later pulls are incremental
            json.loads(spark.helper("pull", r_store, to_air, "spark"))
            s2a, a2s, noop_s2a, noop_a2s = [], [], [], []
            for i in range(1, a.reps + 1):
                spark.helper("commit", r_store, "bench/spark", "spark", str(i))
                s2a.append(timed_pull(l_store, to_spark, "mbp"))
                commit_fulfilment(l_store, "bench/air", "mbp", i)
                a2s.append(json.loads(spark.helper("pull", r_store, to_air, "spark")))
                noop_s2a.append(timed_pull(l_store, to_spark, "mbp"))
                noop_a2s.append(json.loads(spark.helper("pull", r_store, to_air, "spark")))
            results["transport: one fulfilment, Spark -> here (-L)"] = summarize(s2a)
            results["transport: one fulfilment, here -> Spark (-R)"] = summarize(a2s)
            results["transport: no-op pull, Spark -> here"] = summarize(noop_s2a)
            results["transport: no-op pull, here -> Spark"] = summarize(noop_a2s)
            stop(rs)
            stop(ls)

            # -- transport, cold pull of exchange-2 ---------------------------------------------------
            log("transport: cold pull of exchange-2")
            ex2 = f"{a.remote_work}/nodecules/handoff/stores/exchange-2"
            rs = spark.popen(f"NB_BACKEND={a.remote_work}/nodecules/backend {a.remote_python} {a.remote_work}/nodecules/handoff/transport.py serve --store {ex2} --port {p1} --until-stdin-closes")
            start_server(rs, "the Spark's server")
            cold = []
            for i in range(a.reps):
                d = work / f"ex2-{i}"
                cold.append(timed_pull(str(d), to_spark, "mbp"))
            results["transport: cold pull of exchange-2, Spark -> here"] = summarize(cold)
            stop(rs)

            # -- transport, synthetic stores --------------------------------------------------------
            for name, (scopes, commits, nodes) in {"wide (50 scopes x 20 commits x 5 nodes)": (50, 20, 5), "deep (1 scope x 500 commits x 1 node)": (1, 500, 1)}.items():
                key = name.split()[0]
                log(f"transport: synthetic {name}")
                r_big = f"{a.remote_work}/stores/{key}"
                spark.helper("synth", r_big, str(scopes), str(commits), str(nodes))
                size = spark.sh(f"du -sk {r_big} | cut -f1").strip()
                files = spark.sh(f"find {r_big} -type f | wc -l").strip()
                notes.append(f"synthetic {name}: {files} files, {size} KB on the Spark's disk")
                rs = spark.popen(f"NB_BACKEND={a.remote_work}/nodecules/backend {a.remote_python} {a.remote_work}/nodecules/handoff/transport.py serve --store {r_big} --port {p1} --until-stdin-closes")
                start_server(rs, "the Spark's server")
                colds, incs, noops = [], [], []
                for i in range(a.big_reps):
                    d = str(work / f"{key}-{i}")
                    colds.append(timed_pull(d, to_spark, "mbp"))
                    spark.helper("commit", r_big, "synth/000", "spark", str(1000 + i))
                    incs.append(timed_pull(d, to_spark, "mbp"))
                    noops.append(timed_pull(d, to_spark, "mbp"))
                results[f"transport: cold pull, synthetic {name}"] = summarize(colds)
                results[f"transport: one fulfilment into synthetic {key}"] = summarize(incs)
                results[f"transport: no-op pull, synthetic {key}"] = summarize(noops)
                stop(rs)

        # -- git relay, both legs ---------------------------------------------------------------
        if not a.skip_git:
            log("git relay: scratch branch")
            g = work / "git"
            origin = subprocess.run(["git", "-C", str(HERE.parent), "remote", "get-url", "origin"], text=True, capture_output=True).stdout.strip()
            subprocess.run(["git", "clone", "-q", "-b", a.branch, origin, str(g)], check=True)
            gs = g / "handoff" / "stores" / "bench"
            subprocess.run(["git", "-C", str(g), "checkout", "-q", "-b", a.bench_branch], check=True)
            commit_fulfilment(str(gs), "bench/air", "mbp", 0)
            subprocess.run(["git", "-C", str(g), "add", "-A"], check=True)
            subprocess.run(["git", "-C", str(g), "commit", "-q", "-m", "bench: scratch branch for the transport measurement; deleted after"], check=True)
            subprocess.run(["git", "-C", str(g), "push", "-q", "-u", "origin", a.bench_branch], check=True)
            bench_pushed = True
            rclone = f"{a.remote_work}/nodecules"
            spark.sh(f"git -C {rclone} fetch -q origin {a.bench_branch} && git -C {rclone} checkout -q -B {a.bench_branch} FETCH_HEAD")
            relay = str(HERE / "spark-relay.sh")
            env = {**os.environ, "SPARK": a.spark, "SPARK_DIR": rclone, "BRANCH": a.bench_branch, "REPO_DIR": str(g), "GIT_SSH_COMMAND": "ssh -o BatchMode=yes -o ConnectTimeout=8"}
            ups, downs, selfs, noop_ups, pushes, down_only, self_only = [], [], [], [], [], [], []
            failures: List[str] = []
            rstore = f"{rclone}/handoff/stores/bench"
            for i in range(1, a.reps + 1):
                spark.helper("commit", rstore, "bench/spark", "spark", str(i))
                spark.sh(f"git -C {rclone} add -A && git -C {rclone} -c user.name=bench -c user.email=bench@example.invalid commit -q -m 'bench: spark {i}'")
                ups.append(timed_cmd(["bash", relay, "up"], cwd=str(g), env=env))
                spark.sh(f"git -C {rclone} pull -q --rebase origin {a.bench_branch}")  # the Spark takes the rebased branch back
                commit_fulfilment(str(gs), "bench/air", "mbp", 100 + i)
                subprocess.run(["git", "-C", str(g), "add", "-A"], check=True)
                subprocess.run(["git", "-C", str(g), "commit", "-q", "-m", f"bench: here {i}"], check=True)
                push_ms = push(str(g), a.bench_branch, env, failures)
                down = timed_cmd(["bash", relay, "down"], cwd=str(g), env=env)
                pushes.append({"ms": push_ms})
                down_only.append(down)
                downs.append({"ms": push_ms + down["ms"]})
                commit_fulfilment(str(gs), "bench/air", "mbp", 200 + i)
                subprocess.run(["git", "-C", str(g), "add", "-A"], check=True)
                subprocess.run(["git", "-C", str(g), "commit", "-q", "-m", f"bench: here {i}b"], check=True)
                push_ms = push(str(g), a.bench_branch, env, failures)
                inner = json.loads(spark.helper("timed", "git", "-C", rclone, "pull", "-q", "--rebase", "origin", a.bench_branch))
                pushes.append({"ms": push_ms})
                self_only.append(inner)
                selfs.append({"ms": push_ms + inner["ms"]})  # the push, then the Spark's own pull timed on the Spark
                noop_ups.append(timed_cmd(["bash", relay, "up"], cwd=str(g), env=env))
            results["git: one fulfilment, Spark -> GitHub -> here (relay up)"] = summarize(ups)
            results["git: one fulfilment, here -> GitHub + relay down"] = summarize(downs)
            results["git: one fulfilment, here -> GitHub, Spark pulls itself"] = summarize(selfs)
            results["git: no-op relay up"] = summarize(noop_ups)
            results["git, part: push here -> GitHub"] = summarize(pushes)
            results["git, part: relay down alone"] = summarize(down_only)
            results["git, part: the Spark's own pull from GitHub"] = summarize(self_only)
            notes.append(f"push failures retried during the git legs: {len(failures)}" + (f" ({'; '.join(failures)})" if failures else ""))
            notes.append(f"scratch branch {a.bench_branch} pushed for the git legs and deleted afterwards")
    finally:
        if bench_pushed:
            subprocess.run(["git", "-C", str(HERE.parent), "push", "-q", "origin", "--delete", a.bench_branch], capture_output=True)
        tunnel.terminate()
        subprocess.run(["ssh", "-O", "exit", "-o", f"ControlPath={ctl}", a.spark], capture_output=True)
        if not a.keep:
            subprocess.run(["ssh", "-o", "BatchMode=yes", a.spark, f"rm -rf {a.remote_work}"], capture_output=True)
            shutil.rmtree(work, ignore_errors=True)

    lines = [
        f"Measured {time.strftime('%Y-%m-%d %H:%M %Z')} by mbp: this box ({platform.machine()}, macOS {platform.mac_ver()[0] or platform.release()}, Python {platform.python_version()}) "
        f"and the Spark (Python {remote_pyver}), on the LAN; nodecules at {head}. Reps: {a.reps} (synthetic: {a.big_reps}). Times in ms, median (min to max).",
        "",
        "| case | median | min | max | n | manifests | bodies | requests | bytes |",
        "|---|---:|---:|---:|---:|---:|---:|---:|---:|",
    ]
    for k, v in results.items():
        lines.append(
            f"| {k} | {v['median']:.1f} | {v['min']:.1f} | {v['max']:.1f} | {v['n']} | "
            f"{v.get('manifests', '')} | {v.get('bodies', '')} | {v.get('requests', '')} | {v.get('bytes', '')} |"
        )
    lines += [""] + [f"- {n}" for n in notes]
    text = "\n".join(lines) + "\n"
    print(text)
    if a.out:
        Path(a.out).write_text(text)
    return 0


if __name__ == "__main__":
    sys.exit(main())
