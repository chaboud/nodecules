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


def test_the_second_exchange_asks_for_a_tool_call_and_two_judgements(tmp_path: Path):
    """exchange-2 exists because the first round could not exercise tool-call
    parsing or a consideration with facts to judge; its third request states
    the hard limits as eligibility with a schema, after two real models
    ranked an ineligible option (spark, note 0013). With the stand-in model
    no check can pass, and --verify says so: every step answered, zero
    checks passed, exit code 2. Verifying writes nothing to any event log."""
    store = tmp_path / "exchange-2"
    seeded = run(str(HANDOFF / "seed_exchange_2.py"), "--store", str(store))
    assert seeded.returncode == 0, seeded.stderr
    assert "6 pending request(s)" in seeded.stdout and "requests/judge/dest@strict" in seeded.stdout and "requests/judge/dest@filtered" in seeded.stdout and "'trip/plan:constraints/alice'" in seeded.stdout
    before = (store / "events-cloud.jsonl").read_text()
    unanswered = run(str(HANDOFF / "seed_exchange_2.py"), "--store", str(store), "--verify")
    assert unanswered.returncode == 1 and "verify: 0 of 6 answered" in unanswered.stdout
    assert (store / "events-cloud.jsonl").read_text() == before  # a verify from any party appends nothing

    done = run(str(HANDOFF / "fulfil.py"), "--store", str(store), "--provider", "echo", "--by", "spark")
    assert done.returncode == 0, done.stderr
    assert "6 request(s) fulfilled" in done.stdout
    verified = run(str(HANDOFF / "seed_exchange_2.py"), "--store", str(store), "--verify")
    assert verified.returncode == 2, verified.stdout + verified.stderr
    assert "verify: 6 of 6 answered; checks: 0 of 6 passed; failed: reply, judge/dest, judge/dest@strict, judge/dest@prompt-only, judge/dest@schema-only, judge/dest@filtered" in verified.stdout
    assert "FAIL no tool call (stop_reason=end_turn)" in verified.stdout and "FAIL not JSON:" in verified.stdout
    assert (store / "events-cloud.jsonl").read_text() == before
    reloaded = load(store)
    assert all(requests(reloaded, s) == [] for s in reloaded.scopes())
    sent = reloaded.get(reloaded.current("trip/plan"), "judge/dest").data["content"]
    assert "## constraints" in sent and "## decision" in sent  # the echo repeats what the model was shown: both roles rendered
    filtered = reloaded.get(reloaded.current("trip/plan"), "eligible/dest").data
    assert [o["id"] for o in filtered["options"]] == ["o0", "o2", "o5"] and "o8" in filtered["excluded"]  # computed here, no model
    shown = reloaded.get(reloaded.current("trip/plan"), "judge/dest@filtered").data["content"]
    assert "Naples" not in shown.split("## decision")[1].split("## ")[0] if "## decision" in shown else True  # the model never saw it


def test_the_judgement_check_knows_the_hard_limits():
    """The check that passed a wrong answer (note 0013) now computes
    eligibility from the facts: three destinations survive the limits, and
    a ranking that names any other fails even when it is valid JSON."""
    import importlib.util

    spec = importlib.util.spec_from_file_location("seed2", HANDOFF / "seed_exchange_2.py")
    seed2 = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(seed2)
    assert seed2.eligible_ids() == {"o0", "o2", "o5"}  # Lisbon, Oaxaca, Reykjavik
    ok, text = seed2._check_ranking({"content": json.dumps({"ranked": [{"id": "o0", "why": "a"}, {"id": "o2", "why": "b"}, {"id": "o8", "why": "Naples, over budget"}]})})
    assert not ok and "NOT eligible: Naples" in text
    ok, text = seed2._check_ranking({"content": json.dumps({"ranked": [{"id": "o0", "why": "a"}, {"id": "o2", "why": "b"}, {"id": "o5", "why": "c"}]})})
    assert ok and "all three eligible" in text
    ok, text = seed2._check_ranking({"content": "```json\n" + json.dumps({"ranked": [{"id": "o0", "why": "a"}, {"id": "o2", "why": "b"}, {"id": "o5", "why": "c"}]}) + "\n```"})
    assert not ok and "fence" in text  # gemma's shape: right answer, wrong envelope
    ok, text = seed2._check_tools({"tool_calls": [{"name": "lookup_weather", "arguments": {"city": "Porto", "days": 5}}]})
    assert ok
    ok, text = seed2._check_tools({"tool_calls": [{"name": "lookup_weather", "arguments": {"_raw": "{broken"}}]})
    assert not ok


def test_an_exchange_round_trips_over_the_transport_in_both_directions(tmp_path: Path):
    """The real flow without git: the cloud seeds requests; the Spark pulls
    them from the cloud's server and fulfils them (`fulfil.py --peer`); the
    cloud pulls the answers back from the Spark's server; verify sees cache
    hits whose receipts name the Spark."""
    from nodecules.core.disk import DiskBacking
    from nodecules.core.transport import Peer, pull, serve

    cloud, spark = tmp_path / "cloud", tmp_path / "spark"
    assert run(str(HANDOFF / "seed_exchange.py"), "--store", str(cloud)).returncode == 0
    cloud_server = serve(DiskBacking(cloud))
    try:
        done = run(str(HANDOFF / "fulfil.py"), "--store", str(spark), "--peer", cloud_server.url, "--provider", "echo", "--by", "spark")
    finally:
        cloud_server.close()
    assert done.returncode == 0, done.stderr
    assert "pulled from" in done.stdout and "2 request(s) fulfilled" in done.stdout

    spark_server = serve(DiskBacking(spark))
    try:
        back = pull(load(cloud), Peer(spark_server.url), author="cloud")
    finally:
        spark_server.close()
    assert back.retry == {} and back.manifests > 0
    verified = run(str(HANDOFF / "seed_exchange.py"), "--store", str(cloud), "--verify")
    assert verified.returncode == 0, verified.stdout + verified.stderr
    assert "both steps are cache hits" in verified.stdout


def test_the_transport_cli_serves_until_its_stdin_closes_and_pulls(tmp_path: Path):
    """`transport.py serve` prints its URL and lives as long as its stdin
    (so one started over ssh dies with the connection); `transport.py pull`
    reports what it moved."""
    src, dst = tmp_path / "src", tmp_path / "dst"
    assert run(str(HANDOFF / "seed_exchange.py"), "--store", str(src)).returncode == 0
    server = subprocess.Popen(
        [sys.executable, str(HANDOFF / "transport.py"), "serve", "--store", str(src), "--until-stdin-closes"],
        stdin=subprocess.PIPE, stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True, env=ENV,
    )
    try:
        line = server.stdout.readline()
        assert line.startswith("serving ") and "http://127.0.0.1:" in line, line + server.stderr.read()
        url = line.split()[-1]
        pulled = run(str(HANDOFF / "transport.py"), "pull", "--store", str(dst), "--peer", url, "--by", "mbp")
        assert pulled.returncode == 0, pulled.stderr
        assert "manifests" in pulled.stdout and "bodies" in pulled.stdout and "retry 0" in pulled.stdout
        again = run(str(HANDOFF / "transport.py"), "pull", "--store", str(dst), "--peer", url, "--by", "mbp")
        assert "0 manifests, 0 bodies" in again.stdout and "1 requests" in again.stdout
    finally:
        server.stdin.close()
        assert server.wait(timeout=10) == 0
    assert load(dst).current("trip/plan").content_hash() == load(src).current("trip/plan").content_hash()
