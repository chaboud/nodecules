#!/usr/bin/env python3
"""The second exchange: what the first one could not exercise.

The Spark's first report (note 0002) said two things the substrate had
not yet met a real engine on: tool-call parsing (neither recipe asked
for tools), and a consideration with nothing to judge (the options
carried only a name, days, and a price). This store asks for both:

  chat/tools:reply       a question the model should answer by calling a
                         tool; the fulfilled node carries `tool_calls`
                         with parsed arguments, which is the check
  trip/plan:judge/dest   the same sixteen destinations with real facts
                         (season, visa, flight time, notes) and a
                         traveller's constraints, ranked top three with a
                         reason each, as JSON

Produced here with no model, so each becomes a request for
`llm.spark@1`. `--verify` reports whether both came back as cache hits,
whether the tool call's arguments parsed as an object, and whether the
ranking is JSON with three ids from the decision.

    cd backend && PYTHONPATH=. python3 ../handoff/seed_exchange_2.py [--store DIR] [--verify]
"""

from __future__ import annotations

import argparse
import asyncio
import json
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
from nodecules.core.strip_nodes import strip_append  # noqa: E402
from nodecules.core.tracking import JsonlSink, Tracker  # noqa: E402

LIB, BIZ, CHAT = "lib", "trip/plan", "chat/tools"
HANDLE = "llm.spark@1"

FACTS = {
    "Lisbon": {"price": 900, "days": 5, "season": "warm, dry", "visa": "none", "flight_h": 7, "note": "hills, tiles, custard tarts"},
    "Kyoto": {"price": 960, "days": 6, "season": "cherry blossom, crowded", "visa": "none", "flight_h": 14, "note": "temples, kaiseki, early mornings"},
    "Oaxaca": {"price": 1020, "days": 7, "season": "dry, mild", "visa": "none", "flight_h": 6, "note": "mole, mezcal, markets"},
    "Tallinn": {"price": 1080, "days": 8, "season": "cold, long evenings", "visa": "none", "flight_h": 10, "note": "old town, saunas, quiet"},
    "Cape Town": {"price": 1140, "days": 9, "season": "autumn, good hiking", "visa": "none", "flight_h": 18, "note": "wine, table mountain, long haul"},
    "Reykjavik": {"price": 1200, "days": 5, "season": "cold, windy, light returning", "visa": "none", "flight_h": 6, "note": "hot springs, expensive food"},
    "Hanoi": {"price": 1260, "days": 6, "season": "humid, drizzle", "visa": "e-visa", "flight_h": 16, "note": "street food, scooters, noise"},
    "Montreal": {"price": 1320, "days": 7, "season": "thawing, slushy", "visa": "none", "flight_h": 2, "note": "bagels, bilingual, close"},
    "Naples": {"price": 1380, "days": 8, "season": "mild, sunny", "visa": "none", "flight_h": 9, "note": "pizza, Pompeii, chaos"},
    "Ljubljana": {"price": 1440, "days": 9, "season": "cool, green", "visa": "none", "flight_h": 10, "note": "river, lakes, easy walking"},
    "Jaipur": {"price": 1500, "days": 5, "season": "hot, dry", "visa": "e-visa", "flight_h": 15, "note": "forts, textiles, heat"},
    "Valparaiso": {"price": 1560, "days": 6, "season": "autumn, mild", "visa": "none", "flight_h": 12, "note": "murals, hills, poets"},
    "Tbilisi": {"price": 1620, "days": 7, "season": "spring, wine season", "visa": "none", "flight_h": 13, "note": "khachapuri, sulfur baths, cheap wine"},
    "Porto": {"price": 1680, "days": 8, "season": "rainy spells, mild", "visa": "none", "flight_h": 7, "note": "port cellars, river, walkable"},
    "Hokkaido": {"price": 1740, "days": 9, "season": "late snow, quiet", "visa": "none", "flight_h": 15, "note": "onsen, seafood, skiing tail"},
    "Cusco": {"price": 1800, "days": 5, "season": "end of rains", "visa": "none", "flight_h": 11, "note": "altitude, Inca sites, acclimatise"},
}
DEC = Decision(id="dest", prompt="Where should we go this spring?", options=tuple(Option(id=f"o{i}", label=p, facts=f) for i, (p, f) in enumerate(FACTS.items())), needs=("price", "days", "flight_h"), stakes="a week of vacation")
CONSTRAINTS = {"budget": 1300, "max_days": 7, "max_flight_h": 10, "wants": ["food", "walking", "warm"], "avoid": ["crowds", "altitude"]}

TOOLS = [
    {"name": "lookup_weather", "description": "Seven-day forecast for a city.", "parameters": {"type": "object", "properties": {"city": {"type": "string"}, "days": {"type": "integer", "minimum": 1, "maximum": 14}}, "required": ["city"]}},
    {"name": "lookup_flights", "description": "Direct flights between two cities on a date.", "parameters": {"type": "object", "properties": {"origin": {"type": "string"}, "destination": {"type": "string"}, "date": {"type": "string", "format": "date"}}, "required": ["origin", "destination"]}},
]
QUESTION = "What will the weather be in Porto over the next five days? Use the weather tool; do not guess."


def declare(store: Store) -> None:
    if "recipes/tools" in set(store.current(LIB).ids()):
        return
    tx = store.transaction(LIB, author="cloud")
    tx.put(Node(id="recipes/tools", kind=RECIPE_TEMPLATE_KIND, scope=LIB, data={"realization": HANDLE, "params": {"system": "You are an assistant with tools. When a tool answers the question, call it instead of answering from memory.", "tools": TOOLS, "max_tokens": 400}}))
    tx.put(Node(id="recipes/judge", kind=RECIPE_TEMPLATE_KIND, scope=LIB, data={"realization": HANDLE, "params": {"system": "You are helping a traveller choose. Use the decision's option facts and the traveller's constraints. Rank the best three options and give one line of reasoning each. Answer as JSON only: {\"ranked\": [{\"id\": ..., \"why\": ...}, ...]}.", "max_tokens": 800}}))
    tx.commit("recipes for round two")
    tx = store.transaction(BIZ, author="cloud")
    tx.put(Node(id="decisions/dest", kind="decision", scope=BIZ, data=DEC.model_dump(mode="json")))
    tx.put(Node(id="constraints/alice", kind="constraints", scope=BIZ, data=CONSTRAINTS))
    tx.put(Node(id="judge/dest", kind="llm.consideration", scope=BIZ, edges=(Edge(target="decisions/dest", role="decision"), Edge(target="constraints/alice", role="constraints"), Edge(target="recipes/judge", scope=LIB, role=RECIPE_ROLE))))
    tx.commit("a decision with facts, and constraints to judge it by")
    strip_append(store, CHAT, "strips/messages", {"role": "user", "content": QUESTION}, author="cloud")
    tx = store.transaction(CHAT, author="cloud")
    tx.put(Node(id="reply", kind="chat.reply", scope=CHAT, edges=(Edge(target="strips/messages", pattern=AllPattern(), role="messages"), Edge(target="recipes/tools", scope=LIB, role=RECIPE_ROLE))))
    tx.commit("a question that wants a tool call")


def _check_tools(data: dict) -> str:
    calls = data.get("tool_calls") or []
    if not calls:
        return f"no tool call (stop_reason={data.get('stop_reason')}); content: {str(data.get('content'))[:120]!r}"
    c = calls[0]
    args = c.get("arguments")
    ok = isinstance(args, dict) and "_raw" not in args and c.get("name") in {t["name"] for t in TOOLS}
    return f"tool call {c.get('name')} arguments={args!r} · parsed as an object: {'yes' if ok else 'NO'}"


def _check_ranking(data: dict) -> str:
    content = data.get("content") or ""
    try:
        parsed = json.loads(content)
    except json.JSONDecodeError:
        return f"not JSON: {content[:120]!r}"
    ranked = parsed.get("ranked") if isinstance(parsed, dict) else None
    ids = {o.id for o in DEC.options}
    if not isinstance(ranked, list) or len(ranked) != 3 or not all(isinstance(r, dict) and r.get("id") in ids for r in ranked):
        return f"JSON but not three known ids: {content[:160]!r}"
    labels = {o.id: o.label for o in DEC.options}
    return "ranked: " + "; ".join(f"{labels[r['id']]} ({str(r.get('why'))[:60]})" for r in ranked)


async def main(store_dir: str, verify: bool) -> int:
    store = Store()
    tracker = Tracker()
    tracker.attach(store)
    store.attach(DiskBacking(store_dir))
    tracker.add_sink(JsonlSink(Path(store_dir) / "events-cloud.jsonl"))
    declare(store)
    here = Generator(store, [], author="cloud", tracker=tracker, defer=True)
    hits = 0
    for scope, node, check in ((CHAT, "reply", _check_tools), (BIZ, "judge/dest", _check_ranking)):
        g = await here.produce(scope, node)
        line = f"{scope}:{node}: {g.outcome}" + (" (cache hit)" if g.cache_hit else "") + (f" · {g.note}" if g.note else "")
        if g.cache_hit:
            hits += 1
            line += "\n    " + check(g.node.data if isinstance(g.node.data, dict) else {})
        print(line)
    pending = [r for s in store.scopes() for r in requests(store, s)]
    print(f"{len(pending)} pending request(s) in {store_dir}:")
    for r in pending:
        print(f"  {r['request']} wants {r['realization']} · inputs {list(r['inputs'])}")
    if verify:
        ok = hits == 2
        print("verify:", "both steps are cache hits; read the two checks above" if ok else f"{hits} of 2 steps answered")
        return 0 if ok else 1
    return 0


if __name__ == "__main__":
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--store", default=str(HERE / "stores" / "exchange-2"))
    ap.add_argument("--verify", action="store_true")
    a = ap.parse_args()
    sys.exit(asyncio.run(main(a.store, a.verify)))
