#!/usr/bin/env python3
"""Fulfil pending requests in a store from a machine that has the model.

The cloud side declares graphs whose recipes name a realization it does
not have (`llm.spark@1`). Its generator writes a `requests/<node>` node
per such step (core/deferral.py) and moves on. This script is the other
half: load the store, find the requests whose realization is in this
inventory, cook them with a real provider, and commit the answer with
its receipt. The request is removed in the same commit, and the
envelope names who fulfilled it. Back on the cloud side the next
`produce` is a cache hit and the graph continues.

    cd backend && PYTHONPATH=. python3 ../handoff/fulfil.py \\
        --store ../handoff/stores/exchange-1 \\
        --provider openai --base-url http://127.0.0.1:8000 --model <served name> \\
        --by spark --git

    --provider echo            a stand-in model (demos/common.py) for a dry run anywhere
    --provider openai          any OpenAI-compatible endpoint (vLLM, llama.cpp, Ollama, LM Studio)
    --provider pkg.mod:factory a zero-argument factory returning a ToolAwareProvider
    --dry-run                  list what would be fulfilled and stop
    --loop SECS                keep going: pull, fulfil, push, wait, repeat
    --git                      pull --rebase before each round, commit and push the store after it

Nothing here is core: the store's merge rules (content-addressed files,
one head file per candidate, merged on attach) are what make a git
checkout a safe transport. Events go to `<store>/events-<by>.jsonl`;
one file per writer, so two machines never append to the same file.
"""

from __future__ import annotations

import argparse
import asyncio
import importlib
import os
import subprocess
import sys
import time
from pathlib import Path
from typing import Any, Dict, List, Optional

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE.parent / "backend"))
sys.path.insert(0, str(HERE.parent / "demos"))

from nodecules.core.deferral import requests  # noqa: E402
from nodecules.core.disk import load  # noqa: E402
from nodecules.core.generation import Generator, Realization  # noqa: E402
from nodecules.core.llm_providers import ToolAwareProvider  # noqa: E402
from nodecules.core.llm_realization import llm_realization  # noqa: E402
from nodecules.core.tracking import JsonlSink, Tracker  # noqa: E402


def make_provider(a: argparse.Namespace) -> ToolAwareProvider:
    if a.provider == "echo":
        from common import EchoProvider  # demos/common.py

        return EchoProvider()
    if a.provider == "openai":
        from nodecules.core.openai_compatible import OpenAICompatibleProvider

        if not a.base_url:
            raise SystemExit("--provider openai needs --base-url")
        key = a.api_key or ""
        if key.startswith("env:"):
            key = os.environ.get(key[4:], "")
        return OpenAICompatibleProvider(a.base_url, api_key=key, timeout_s=a.timeout)
    if ":" in a.provider:
        mod, fn = a.provider.split(":", 1)
        return getattr(importlib.import_module(mod), fn)()
    raise SystemExit(f"unknown provider {a.provider!r}")


def make_realizations(provider: ToolAwareProvider, handles: List[str], model: Optional[str]) -> List[Realization]:
    out: List[Realization] = []
    for h in handles:
        base = llm_realization(provider, h, default_model=model or "default")
        if model:
            # The operator's served model name wins over whatever the recipe says.
            async def cook(inputs: Dict[str, Any], params: Dict[str, Any], _base: Realization = base) -> Any:
                return await _base.cook(inputs, {**params, "model": model})

            out.append(Realization(handle=h, cook=cook, deterministic=False))
        else:
            out.append(base)
    return out


def git(repo: Path, *args: str, check: bool = True) -> subprocess.CompletedProcess:
    return subprocess.run(["git", "-C", str(repo), *args], text=True, capture_output=True, check=check)


def repo_of(store: Path) -> Optional[Path]:
    try:
        top = git(store, "rev-parse", "--show-toplevel").stdout.strip()
        return Path(top) if top else None
    except (subprocess.CalledProcessError, FileNotFoundError):
        return None


async def round_once(a: argparse.Namespace, realizations: List[Realization]) -> int:
    store_dir = Path(a.store)
    store = load(store_dir)
    tracker = Tracker()
    tracker.attach(store)
    if not a.dry_run:
        tracker.add_sink(JsonlSink(store_dir / f"events-{a.by}.jsonl"))
    handles = {r.handle for r in realizations}
    gen = Generator(store, realizations, author=a.by, tracker=tracker, defer=True)
    scopes = a.scope or list(store.scopes())
    done = 0
    for scope in scopes:
        for req in requests(store, scope):
            if req["realization"] not in handles:
                print(f"  skip  {scope}:{req['for']} wants {req['realization']} (not in inventory {sorted(handles)})")
                continue
            if a.dry_run:
                print(f"  would fulfil {scope}:{req['for']} with {req['realization']} · inputs {list(req['inputs'])}")
                done += 1
                continue
            t0 = time.monotonic()
            g = await gen.produce(scope, req["for"])
            ms = int((time.monotonic() - t0) * 1000)
            if g.cooked:
                done += 1
                preview = g.node.data.get("content") if isinstance(g.node.data, dict) else g.node.data
                print(f"  fulfilled {scope}:{req['for']} in {ms} ms · {str(preview)[:96]!r}")
            else:
                print(f"  {g.outcome:9} {scope}:{req['for']} · {g.error or g.note or ''}")
    return done


def commit_and_push(repo: Path, store: Path, by: str, n: int) -> None:
    rel = store.resolve().relative_to(repo.resolve())
    git(repo, "add", "-A", str(rel))
    if not git(repo, "status", "--porcelain", "--", str(rel)).stdout.strip():
        print("  nothing to commit")
        return
    git(repo, "commit", "-q", "-m", f"{by}: fulfil {n} request(s) in {rel}")
    for attempt in range(3):
        r = git(repo, "push", "-q", check=False)
        if r.returncode == 0:
            print(f"  pushed ({rel})")
            return
        git(repo, "pull", "--rebase", "-q", check=False)
    print(f"  push failed after retries: {r.stderr.strip()}", file=sys.stderr)


async def main(a: argparse.Namespace) -> int:
    provider = make_provider(a)
    realizations = make_realizations(provider, a.handle, a.model)
    repo = repo_of(Path(a.store)) if a.git else None
    if a.git and repo is None:
        raise SystemExit("--git needs the store inside a git checkout")
    while True:
        if repo is not None:
            r = git(repo, "pull", "--rebase", "-q", check=False)
            if r.returncode != 0:
                print(f"  pull failed: {r.stderr.strip()}", file=sys.stderr)
        n = await round_once(a, realizations)
        print(f"round: {n} request(s) {'listed' if a.dry_run else 'fulfilled'} in {a.store}")
        if repo is not None and n and not a.dry_run:
            commit_and_push(repo, Path(a.store), a.by, n)
        if not a.loop or a.dry_run:
            return 0
        time.sleep(a.loop)  # a poll, not a signal; a watcher on the inbox is a later slice


if __name__ == "__main__":
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--store", required=True)
    ap.add_argument("--scope", action="append", help="limit to these scopes (default: all)")
    ap.add_argument("--provider", default="echo")
    ap.add_argument("--base-url")
    ap.add_argument("--api-key", help="a key, or env:NAME")
    ap.add_argument("--model", help="the served model name; overrides the recipe's")
    ap.add_argument("--timeout", type=float, default=120.0)
    ap.add_argument("--handle", action="append", default=None, help="realization handle(s) this machine offers (default: llm.spark@1)")
    ap.add_argument("--by", required=True, help="who is fulfilling: goes on every manifest and receipt")
    ap.add_argument("--dry-run", action="store_true")
    ap.add_argument("--loop", type=float, default=0.0, metavar="SECS")
    ap.add_argument("--git", action="store_true")
    args = ap.parse_args()
    if not args.handle:
        args.handle = ["llm.spark@1"]
    sys.exit(asyncio.run(main(args)))
