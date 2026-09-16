#!/usr/bin/env python3
"""Seed an exchange store with work that needs a model: the cloud side's
half of the first round with the Spark.

Writes two graphs into `--store` (default `handoff/stores/exchange-1`)
and produces them with a generator that has no model, so each step
becomes a `requests/<node>` node:

  trip/plan:consider/dest   sixteen destinations to summarise (a decision,
                            not a chat, in front of the model)
  chat/spark:reply          one message from the cloud session to the
                            Spark's model, through the chat graph shape

Re-running is safe: an existing request is left alone ("already
requested"). Fulfil with `handoff/fulfil.py`; verify with
`--verify`, which produces the same nodes again with no model and
reports whether each is a cache hit.

    cd backend && PYTHONPATH=. python3 ../handoff/seed_exchange.py [--store DIR] [--verify]
"""

from __future__ import annotations

import argparse
import asyncio
import sys
from pathlib import Path

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE.parent / "backend"))

from nodecules.core.decisions import Decision, Option  # noqa: E402
from nodecules.core.deferral import requests  # noqa: E402
from nodecules.core.disk import DiskBacking  # noqa: E402
from nodecules.core.generation import RECIPE_ROLE, RECIPE_TEMPLATE_KIND, Generator  # noqa: E402
from nodecules.core.store import Edge, Node, Store  # noqa: E402
from nodecules.core.strip_access import AllPattern  # noqa: E402
from nodecules.core.strip_nodes import strip_append, strip_read  # noqa: E402
from nodecules.core.tracking import JsonlSink, Tracker  # noqa: E402

LIB, BIZ, CHAT = "lib", "trip/plan", "chat/spark"
HANDLE = "llm.spark@1"
PLACES = ["Lisbon", "Kyoto", "Oaxaca", "Tallinn", "Cape Town", "Reykjavik", "Hanoi", "Montreal", "Naples", "Ljubljana", "Jaipur", "Valparaiso", "Tbilisi", "Porto", "Hokkaido", "Cusco"]
DEC = Decision(id="dest", prompt="Where should we go this spring?", options=tuple(Option(id=f"o{i}", label=p, facts={"price": 900 + 60 * i, "days": 5 + i % 5}) for i, p in enumerate(PLACES)), needs=("price", "days"), stakes="a week of vacation")

FIRST_MESSAGE = (
    "Hello from the cloud session. You are the first real model to answer through this graph. "
    "In three sentences: what is one thing about running inference on a DGX Spark that the "
    "substrate's cost model (nodecules/backend/nodecules/core/placement.py) should know and does not?"
)


def declare(store: Store) -> None:
    if "recipes/consider" in set(store.current(LIB).ids()):
        return
    tx = store.transaction(LIB, author="cloud")
    tx.put(Node(id="recipes/consider", kind=RECIPE_TEMPLATE_KIND, scope=LIB, data={"realization": HANDLE, "params": {"system": "You are helping a traveller choose. For each option in the decision, write one line they would want to read. Answer as JSON: an object from option id to line.", "max_tokens": 1200}}))
    tx.put(Node(id="recipes/reply", kind=RECIPE_TEMPLATE_KIND, scope=LIB, data={"realization": HANDLE, "params": {"system": "You are a model on a DGX Spark answering a Claude Code session in the cloud. Be concrete and brief.", "max_tokens": 600}}))
    tx.commit("recipes that need a model")
    tx = store.transaction(BIZ, author="cloud")
    tx.put(Node(id="decisions/dest", kind="decision", scope=BIZ, data=DEC.model_dump(mode="json")))
    tx.put(Node(id="consider/dest", kind="llm.consideration", scope=BIZ, edges=(Edge(target="decisions/dest", role="decision"), Edge(target="recipes/consider", scope=LIB, role=RECIPE_ROLE))))
    tx.commit("a decision to consider")
    strip_append(store, CHAT, "strips/messages", {"role": "user", "content": FIRST_MESSAGE}, author="cloud")
    tx = store.transaction(CHAT, author="cloud")
    tx.put(Node(id="reply", kind="chat.reply", scope=CHAT, edges=(Edge(target="strips/messages", pattern=AllPattern(), role="messages"), Edge(target="recipes/reply", scope=LIB, role=RECIPE_ROLE))))
    tx.commit("a message for the Spark")


async def main(store_dir: str, verify: bool) -> int:
    store = Store()
    tracker = Tracker()
    tracker.attach(store)
    store.attach(DiskBacking(store_dir))
    tracker.add_sink(JsonlSink(Path(store_dir) / "events-cloud.jsonl"))
    declare(store)
    here = Generator(store, [], author="cloud", tracker=tracker, defer=True)  # no model here
    hits = 0
    for scope, node in ((BIZ, "consider/dest"), (CHAT, "reply")):
        g = await here.produce(scope, node)
        line = f"{scope}:{node}: {g.outcome}" + (" (cache hit)" if g.cache_hit else "") + (f" · {g.note}" if g.note else "")
        if g.cache_hit:
            hits += 1
            content = g.node.data.get("content") if isinstance(g.node.data, dict) else g.node.data
            line += f"\n    answer: {str(content)[:200]!r}"
        print(line)
    pending = [r for s in store.scopes() for r in requests(store, s)]
    print(f"{len(pending)} pending request(s) in {store_dir}:")
    for r in pending:
        print(f"  {r['request']} wants {r['realization']} · inputs {list(r['inputs'])}")
    if verify:
        ok = hits == 2
        print("verify:", "both steps are cache hits; the round trip worked" if ok else f"{hits} of 2 steps answered")
        return 0 if ok else 1
    print(f"turns so far in {CHAT}: {len(strip_read(store, store.current(CHAT), 'strips/messages'))}")
    return 0


if __name__ == "__main__":
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--store", default=str(HERE / "stores" / "exchange-1"))
    ap.add_argument("--verify", action="store_true", help="report whether the requests have been answered")
    a = ap.parse_args()
    sys.exit(asyncio.run(main(a.store, a.verify)))
