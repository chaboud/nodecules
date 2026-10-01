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

  trip/plan:judge/dest@strict  the same judgement with the hard limits
                         stated as eligibility and a response_schema on
                         the recipe (the Spark's suggestions in note
                         0013, after both models ranked an ineligible
                         option and one wrapped its JSON in a fence)

Produced here with no model, so each becomes a request for
`llm.spark@1`. `--verify` reports, per step, whether it came back as a
cache hit and whether its check passed: the tool call's arguments parsed
as an object; the ranking is JSON, three known ids, and every id is
eligible under the hard limits (computed from the facts, not assumed).
The last line says how many steps answered and how many checks passed;
the exit code is 0 only when all did (1: a step is unanswered; 2: a
check failed). Verifying writes nothing: no event sink is attached, so a
party other than cloud can run it without appending to cloud's log.

    cd backend && PYTHONPATH=. python3 ../handoff/seed_exchange_2.py [--store DIR] [--verify] [--by cloud]
"""

from __future__ import annotations

import argparse
import asyncio
import json
import re
import sys
from pathlib import Path
from typing import Tuple

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


JUDGE_SCHEMA = {
    "type": "object",
    "properties": {"ranked": {"type": "array", "minItems": 3, "maxItems": 3, "items": {"type": "object", "properties": {"id": {"type": "string"}, "why": {"type": "string"}}, "required": ["id", "why"]}}},
    "required": ["ranked"],
}
STRICT_SYSTEM = (
    "You are helping a traveller choose. The constraints budget, max_days, and max_flight_h are hard limits: "
    "an option whose price exceeds budget, whose days exceed max_days, or whose flight_h exceeds max_flight_h is not eligible "
    "and must not appear in the ranking, whatever its other merits. The wants and avoids are soft and only order the eligible options. "
    "Rank the three best eligible options with one line of reasoning each."
)


def eligible_ids() -> set:
    """The ids the hard limits leave, computed from the facts."""
    return {o.id for o in DEC.options if o.facts["price"] <= CONSTRAINTS["budget"] and o.facts["days"] <= CONSTRAINTS["max_days"] and o.facts["flight_h"] <= CONSTRAINTS["max_flight_h"]}


def declare(store: Store) -> None:
    have = set(store.current(LIB).ids())
    if "recipes/judge-strict" not in have:
        tx = store.transaction(LIB, author="cloud")
        tx.put(Node(id="recipes/judge-strict", kind=RECIPE_TEMPLATE_KIND, scope=LIB, data={"realization": HANDLE, "params": {"system": STRICT_SYSTEM, "response_schema": JUDGE_SCHEMA, "max_tokens": 800}}))
        tx.commit("a judge recipe with eligibility stated and a schema enforced")
        tx = store.transaction(BIZ, author="cloud")
        if "decisions/dest" not in set(store.current(BIZ).ids()):
            tx.put(Node(id="decisions/dest", kind="decision", scope=BIZ, data=DEC.model_dump(mode="json")))
            tx.put(Node(id="constraints/alice", kind="constraints", scope=BIZ, data=CONSTRAINTS))
        tx.put(Node(id="judge/dest@strict", kind="llm.consideration", scope=BIZ, edges=(Edge(target="decisions/dest", role="decision"), Edge(target="constraints/alice", role="constraints"), Edge(target="recipes/judge-strict", scope=LIB, role=RECIPE_ROLE))))
        tx.commit("the same judgement, hard limits as eligibility")
    if "recipes/tools" in have:
        return
    tx = store.transaction(LIB, author="cloud")
    tx.put(Node(id="recipes/tools", kind=RECIPE_TEMPLATE_KIND, scope=LIB, data={"realization": HANDLE, "params": {"system": "You are an assistant with tools. When a tool answers the question, call it instead of answering from memory.", "tools": TOOLS, "max_tokens": 400}}))
    tx.put(Node(id="recipes/judge", kind=RECIPE_TEMPLATE_KIND, scope=LIB, data={"realization": HANDLE, "params": {"system": "You are helping a traveller choose. Use the decision's option facts and the traveller's constraints. Rank the best three options and give one line of reasoning each. Answer as JSON only: {\"ranked\": [{\"id\": ..., \"why\": ...}, ...]}.", "max_tokens": 800}}))
    tx.commit("recipes for round two")
    tx = store.transaction(BIZ, author="cloud")
    if "decisions/dest" not in set(store.current(BIZ).ids()):
        tx.put(Node(id="decisions/dest", kind="decision", scope=BIZ, data=DEC.model_dump(mode="json")))
        tx.put(Node(id="constraints/alice", kind="constraints", scope=BIZ, data=CONSTRAINTS))
    tx.put(Node(id="judge/dest", kind="llm.consideration", scope=BIZ, edges=(Edge(target="decisions/dest", role="decision"), Edge(target="constraints/alice", role="constraints"), Edge(target="recipes/judge", scope=LIB, role=RECIPE_ROLE))))
    tx.commit("a decision with facts, and constraints to judge it by")
    strip_append(store, CHAT, "strips/messages", {"role": "user", "content": QUESTION}, author="cloud")
    tx = store.transaction(CHAT, author="cloud")
    tx.put(Node(id="reply", kind="chat.reply", scope=CHAT, edges=(Edge(target="strips/messages", pattern=AllPattern(), role="messages"), Edge(target="recipes/tools", scope=LIB, role=RECIPE_ROLE))))
    tx.commit("a question that wants a tool call")


def _check_tools(data: dict) -> Tuple[bool, str]:
    calls = data.get("tool_calls") or []
    if not calls:
        return False, f"no tool call (stop_reason={data.get('stop_reason')}); content: {str(data.get('content'))[:120]!r}"
    c = calls[0]
    args = c.get("arguments")
    ok = isinstance(args, dict) and "_raw" not in args and c.get("name") in {t["name"] for t in TOOLS} and "city" in args
    return ok, f"tool call {c.get('name')} arguments={args!r} · parsed as an object with a city: {'yes' if ok else 'NO'}"


def _check_ranking(data: dict) -> Tuple[bool, str]:
    content = data.get("content") or ""
    fenced = content.strip().startswith("```")
    body = re.sub(r"^```[a-zA-Z]*\s*|\s*```$", "", content.strip()) if fenced else content
    try:
        parsed = json.loads(body)
    except json.JSONDecodeError:
        return False, f"not JSON{' (in a ``` fence, still not JSON)' if fenced else ''}: {content[:120]!r}"
    ranked = parsed.get("ranked") if isinstance(parsed, dict) else None
    ids = {o.id for o in DEC.options}
    if not isinstance(ranked, list) or len(ranked) != 3 or not all(isinstance(r, dict) and r.get("id") in ids for r in ranked):
        return False, f"JSON but not three known ids: {content[:160]!r}"
    labels = {o.id: o.label for o in DEC.options}
    eligible = eligible_ids()
    bad = [r["id"] for r in ranked if r["id"] not in eligible]
    text = "ranked: " + "; ".join(f"{labels[r['id']]} ({str(r.get('why'))[:60]})" for r in ranked)
    if fenced:
        text += " · JSON arrived in a ``` fence (the recipe said JSON only)"
    if bad:
        return False, text + f" · NOT eligible: {', '.join(labels[b] for b in bad)}; the hard limits leave {sorted(eligible)}"
    return not fenced, text + f" · all three eligible ({sorted(eligible)})"


STEPS = ((CHAT, "reply", _check_tools), (BIZ, "judge/dest", _check_ranking), (BIZ, "judge/dest@strict", _check_ranking))


async def main(store_dir: str, verify: bool, by: str) -> int:
    store = Store()
    tracker = Tracker()
    tracker.attach(store)
    store.attach(DiskBacking(store_dir))
    if not verify:
        tracker.add_sink(JsonlSink(Path(store_dir) / f"events-{by}.jsonl"))  # verifying writes nothing, whoever runs it
    declare(store)
    here = Generator(store, [], author=by, tracker=tracker, defer=True)
    hits, passed, failed = 0, 0, []
    for scope, node, check in STEPS:
        g = await here.produce(scope, node)
        line = f"{scope}:{node}: {g.outcome}" + (" (cache hit)" if g.cache_hit else "") + (f" · {g.note}" if g.note else "")
        if g.cache_hit:
            hits += 1
            ok, text = check(g.node.data if isinstance(g.node.data, dict) else {})
            passed += ok
            if not ok:
                failed.append(node)
            line += "\n    " + ("PASS " if ok else "FAIL ") + text
        print(line)
    pending = [r for s in store.scopes() for r in requests(store, s)]
    print(f"{len(pending)} pending request(s) in {store_dir}:")
    for r in pending:
        print(f"  {r['request']} wants {r['realization']} · inputs {list(r['inputs'])}")
    if verify:
        n = len(STEPS)
        print(f"verify: {hits} of {n} answered; checks: {passed} of {hits} passed" + (f"; failed: {', '.join(failed)}" if failed else ""))
        return 0 if (hits == n and passed == hits) else (1 if hits < n else 2)
    return 0


if __name__ == "__main__":
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--store", default=str(HERE / "stores" / "exchange-2"))
    ap.add_argument("--verify", action="store_true")
    ap.add_argument("--by", default="cloud", help="who is seeding; names the event log (not written when verifying)")
    a = ap.parse_args()
    sys.exit(asyncio.run(main(a.store, a.verify, a.by)))
