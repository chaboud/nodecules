"""The hand-off between machines, end to end on one machine: the cloud side
seeds a store with steps that need a model; a worker with a (stand-in)
model fulfils them through the same directory; back on the cloud side
they are cache hits. Also the note-passing CLI's round trip."""

from __future__ import annotations

import json
import os
import subprocess
import sys
from pathlib import Path

import pytest

from nodecules.core.deferral import requests
from nodecules.core.disk import load
from nodecules.core.store import envelope_id

HANDOFF = Path(__file__).resolve().parents[3] / "handoff"
ENV = {**os.environ, "PYTHONPATH": str(Path(__file__).resolve().parents[2])}


def run(*args: str, stdin: str = "") -> subprocess.CompletedProcess:
    return subprocess.run([sys.executable, *args], input=stdin, text=True, capture_output=True, env=ENV, timeout=120)


def test_seed_then_fulfil_with_a_stand_in_model_then_verify_cache_hits(tmp_path: Path):
    store = tmp_path / "exchange"
    seeded = run(str(HANDOFF / "seed_exchange.py"), "--store", str(store))
    assert seeded.returncode == 0, seeded.stderr
    assert "2 pending request(s)" in seeded.stdout and "requests/consider/dest" in seeded.stdout and "requests/reply" in seeded.stdout
    assert (store / "events-cloud.jsonl").exists()

    listed = run(str(HANDOFF / "fulfil.py"), "--store", str(store), "--provider", "echo", "--by", "spark", "--dry-run")
    assert listed.returncode == 0, listed.stderr
    assert "would fulfil trip/plan:consider/dest" in listed.stdout and "2 request(s) listed" in listed.stdout
    assert not (store / "events-spark.jsonl").exists()  # a dry run writes nothing

    done = run(str(HANDOFF / "fulfil.py"), "--store", str(store), "--provider", "echo", "--by", "spark")
    assert done.returncode == 0, done.stderr
    assert "fulfilled trip/plan:consider/dest" in done.stdout and "fulfilled chat/spark:reply" in done.stdout
    assert "2 request(s) fulfilled" in done.stdout

    # The worker's events are its own file; the cloud's are untouched.
    spark_events = [json.loads(l) for l in (store / "events-spark.jsonl").read_text().splitlines()]
    assert any(e["kind"] == "produce.cooked" for e in spark_events)
    assert all(e["detail"].get("author") == "spark" for e in spark_events if e["kind"] == "commit")

    reloaded = load(store)
    assert all(requests(reloaded, s) == [] for s in reloaded.scopes())
    env = reloaded.get(reloaded.current("chat/spark"), envelope_id("reply")).data
    assert env["recipe"]["fulfilled_by"] == "spark" and env["recipe"]["realization"] == "llm.spark@1"

    verified = run(str(HANDOFF / "seed_exchange.py"), "--store", str(store), "--verify")
    assert verified.returncode == 0, verified.stdout + verified.stderr
    assert "both steps are cache hits" in verified.stdout and "0 pending request(s)" in verified.stdout

    again = run(str(HANDOFF / "fulfil.py"), "--store", str(store), "--provider", "echo", "--by", "spark")
    assert "0 request(s) fulfilled" in again.stdout  # nothing left to do, nothing rewritten


def test_notes_round_trip(tmp_path: Path):
    root = tmp_path / "handoff"
    root.mkdir()
    note = str(HANDOFF / "note.py")
    made = run(note, "--root", str(root), "new", "--from", "cloud", "--to", "spark", "--kind", "ask", "--re", "First round: fulfil exchange-1", "--date", "2026-09-18", stdin="Please run fulfil.py on exchange-1.\n")
    assert made.returncode == 0, made.stderr
    path = root / "inbox" / "spark" / "0001-first-round-fulfil-exchange-1.md"
    assert path.exists()
    text = path.read_text()
    assert text.startswith("---\nid: 0001\nfrom: cloud\nto: spark\ndate: 2026-09-18\nkind: ask\nre: First round: fulfil exchange-1\nstatus: open\n---\n")
    assert "reply-to" not in text  # empty fields are not written

    sched = run(note, "--root", str(root), "new", "--from", "cloud", "--to", "spark", "--kind", "schedule", "--re", "poll exchange-1", "--every", "6h", "--until", "2026-10-01", "--body", "Run fulfil.py --git once.")
    assert sched.returncode == 0 and (root / "inbox" / "spark" / "0002-poll-exchange-1.md").exists()
    bad = run(note, "--root", str(root), "new", "--from", "cloud", "--to", "spark", "--kind", "schedule", "--re", "x", "--body", "y")
    assert bad.returncode == 2 and "--every" in bad.stderr

    listed = run(note, "--root", str(root), "list", "--to", "spark")
    assert "0001  open   cloud  -> spark   ask" in listed.stdout and "every 6h until 2026-10-01" in listed.stdout

    closed = run(note, "--root", str(root), "done", "0001", "--by", "spark", "--note", "ran it, two fulfilled", "--date", "2026-09-19")
    assert closed.returncode == 0, closed.stderr
    assert not path.exists() and (root / "done" / path.name).exists()
    meta = (root / "done" / path.name).read_text()
    assert "status: done\n" in meta and "closed: 2026-09-19 by spark: ran it, two fulfilled\n" in meta
    assert "0001" not in run(note, "--root", str(root), "list").stdout
    assert "0001  done" in run(note, "--root", str(root), "list", "--all").stdout
    assert run(note, "--root", str(root), "show", "0001").stdout.startswith("---\nid: 0001")


def test_the_second_exchange_asks_for_a_tool_call_and_a_judgement(tmp_path: Path):
    """exchange-2 exists because the first round could not exercise tool-call
    parsing or a consideration with facts to judge. With the stand-in
    model neither check can pass, and --verify says so without failing:
    both steps are cache hits, and the checks report what came back."""
    store = tmp_path / "exchange-2"
    seeded = run(str(HANDOFF / "seed_exchange_2.py"), "--store", str(store))
    assert seeded.returncode == 0, seeded.stderr
    assert "2 pending request(s)" in seeded.stdout and "requests/judge/dest" in seeded.stdout and "'trip/plan:constraints/alice'" in seeded.stdout
    done = run(str(HANDOFF / "fulfil.py"), "--store", str(store), "--provider", "echo", "--by", "spark")
    assert done.returncode == 0, done.stderr
    assert "2 request(s) fulfilled" in done.stdout
    verified = run(str(HANDOFF / "seed_exchange_2.py"), "--store", str(store), "--verify")
    assert verified.returncode == 0, verified.stdout + verified.stderr
    assert "both steps are cache hits" in verified.stdout
    assert "no tool call (stop_reason=end_turn)" in verified.stdout and "not JSON:" in verified.stdout
    reloaded = load(store)
    assert all(requests(reloaded, s) == [] for s in reloaded.scopes())
    sent = reloaded.get(reloaded.current("trip/plan"), "judge/dest").data["content"]
    assert "## constraints" in sent and "## decision" in sent  # the echo repeats what the model was shown: both roles rendered
