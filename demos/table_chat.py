"""A chat with a table: a decision presented to a person, answered through
the table, advancing the business graph exactly once; and a step that
needs a model nobody here has, deferred as a request that something with
a model fulfils.

Alice asks the butler to plan a trip. The butler turns the sixteen
candidate destinations into a decision node. The same decision is
presented three ways: on her phone (four at a time, a tournament), on the
wall display (all sixteen at once), and for a screen reader (spoken
form). Alice answers through her input strip; each answer recooks the
presentation; the business step advances once, when the choice is final.
Then the butler wants each option summarised by a model. None is loaded
here, so the summarising step becomes a request node in the store; a
second agent — standing in for a Claude Code instance on a machine with
an inference engine — reads the request, produces the answer, and fulfils
it. Back here it is a cache hit and the plan continues.

Does not show: a real screen (elements are rendered as text), a real
model (the fulfilling agent is scripted), a network (the second agent
shares the store in-process; on a shared directory or a synced replica it
would be another machine).

    cd backend && PYTHONPATH=. python3 ../demos/table_chat.py [--store DIR]
"""

from __future__ import annotations

import argparse
import asyncio
import os
import sys

sys.path.insert(0, os.path.join(os.path.dirname(__file__)))

from common import text_frame  # noqa: E402

from nodecules.core.decisions import Decision, Option, advance_realization, presentation_realization, step_element  # noqa: E402
from nodecules.core.deferral import fulfill, requests  # noqa: E402
from nodecules.core.disk import DiskBacking  # noqa: E402
from nodecules.core.generation import PARAMS_ROLE, RECIPE_ROLE, RECIPE_TEMPLATE_KIND, Generator  # noqa: E402
from nodecules.core.scene import Transition, frame  # noqa: E402
from nodecules.core.store import Edge, Node, Store  # noqa: E402
from nodecules.core.strip_access import AllPattern  # noqa: E402
from nodecules.core.strip_nodes import strip_append  # noqa: E402
from nodecules.core.tracking import JsonlSink, Tracker  # noqa: E402

BIZ, TABLE, LIB = "trip/plan", "table/alice", "lib"
MS = 10_000
PLACES = ["Lisbon", "Kyoto", "Oaxaca", "Tallinn", "Cape Town", "Reykjavik", "Hanoi", "Montreal", "Naples", "Ljubljana", "Jaipur", "Valparaiso", "Tbilisi", "Porto", "Hokkaido", "Cusco"]
DEC = Decision(id="dest", prompt="Where should we go this spring?", options=tuple(Option(id=f"o{i}", label=p, facts={"price": 900 + 60 * i, "days": 5 + i % 5}) for i, p in enumerate(PLACES)), needs=("price", "days"), stakes="a week of vacation")


def declare(store: Store) -> None:
    tx = store.transaction(LIB, author="dev")
    tx.put(Node(id="recipes/present", kind=RECIPE_TEMPLATE_KIND, scope=LIB, data={"realization": "present.choice@1", "params": {}}))
    tx.put(Node(id="recipes/advance", kind=RECIPE_TEMPLATE_KIND, scope=LIB, data={"realization": "decision.advance@1", "params": {}}))
    tx.put(Node(id="recipes/consider", kind=RECIPE_TEMPLATE_KIND, scope=LIB, data={"realization": "llm.spark@1", "params": {"system": "Summarise each option in one line for a traveller."}}))
    tx.commit("recipes")
    tx = store.transaction(BIZ, author="butler")
    tx.put(Node(id="decisions/dest", kind="decision", scope=BIZ, data=DEC.model_dump(mode="json")))
    for sid, params in (("phone", {"max_options": 4}), ("wall", {"max_options": 16}), ("reader", {"max_options": 4, "non_visual": True})):
        tx.put(Node(id=f"surfaces/{sid}", kind="params", scope=BIZ, data=params))
        tx.put(Node(id=f"present/dest@{sid}", kind="presentation", scope=BIZ, edges=(Edge(target="decisions/dest", role="decision"), Edge(target="strips/answers/alice", pattern=AllPattern(), role="answers", optional=True), Edge(target="recipes/present", scope=LIB, role=RECIPE_ROLE), Edge(target=f"surfaces/{sid}", role=PARAMS_ROLE))))
    tx.put(Node(id="plan/next", kind="business.step", scope=BIZ, edges=(Edge(target="present/dest@phone", role="choice"), Edge(target="recipes/advance", scope=LIB, role=RECIPE_ROLE))))
    tx.put(Node(id="consider/dest", kind="llm.consideration", scope=BIZ, edges=(Edge(target="decisions/dest", role="decision"), Edge(target="recipes/consider", scope=LIB, role=RECIPE_ROLE))))
    tx.commit("the butler declares the decision and its presentations")


async def show(store: Store, g: Generator, sid: str, t: int) -> None:
    out = await g.produce(BIZ, f"present/dest@{sid}")
    from nodecules.core.decisions import Presentation

    p = Presentation(**out.node.data)
    el = step_element(p, present_at=t, expires_at=t + 30_000 * MS, enter=Transition(kind="fade", duration=200 * MS))
    if el is None:
        print(f"  [{sid}] done: chose {p.chosen} via {[s['chosen'] for s in p.path]}")
        return
    tx = store.transaction(TABLE, author="butler")
    tx.put(Node(id=f"step/{sid}", kind="ui.choice", scope=TABLE, data=el.model_dump()))
    m = tx.commit(f"show {p.step.id} on {sid}")
    fr = frame(store, m, t, timeline="table")
    line = [e for e in fr.elements if e.id == f"step/{sid}"][0]
    opts = ", ".join(o["label"] for o in line.payload["options"])
    print(f"  [{sid}] {line.payload['step']} ({p.mode}): {opts}" + (f"\n         spoken: {line.payload['spoken']}" if line.payload.get("spoken") else ""))


async def main(store_dir: str | None) -> None:
    store = Store()
    tracker = Tracker()
    tracker.attach(store)
    if store_dir:
        store.attach(DiskBacking(store_dir))
        tracker.add_sink(JsonlSink(os.path.join(store_dir, "events.jsonl")))
    declare(store)
    here = Generator(store, [presentation_realization(), advance_realization()], author="butler", tracker=tracker, defer=True)

    print("alice: help me plan a trip this spring")
    print("butler: I have sixteen candidates. Choosing on your phone four at a time; the wall shows all of them.\n")
    t = 1_000 * MS
    for sid in ("phone", "wall", "reader"):
        await show(store, here, sid, t)
    advanced = 0
    for step, pick in [("r1g0", "o1"), ("r1g1", "o6"), ("r1g2", "o9"), ("r1g3", "o13"), ("final", "o9")]:
        strip_append(store, BIZ, "strips/answers/alice", {"step": step, "chosen": pick}, author="alice")
        t += 3_000 * MS
        print(f"\nalice answers {step}: {DEC.options[int(pick[1:])].label}")
        await show(store, here, "phone", t)
        out = await here.produce(BIZ, "plan/next")
        if out.cooked and out.node.data.get("advanced"):
            advanced += 1
            print(f"  business graph advanced: {out.node.data}")
    print(f"\nthe business step advanced {advanced} time(s)")

    print("\nbutler: I want each option summarised by a model. None is loaded here.")
    out = await here.produce(BIZ, "consider/dest")
    print(f"  outcome={out.outcome} request={out.request}")
    for r in requests(store, BIZ):
        print(f"  pending: {r['request']} wants {r['realization']} with inputs {list(r['inputs'])}")

    print("\n(elsewhere: an agent with an inference engine reads the request and fulfils it)")
    summaries = {o.id: f"{o.label}: about {o.facts['price']} for {o.facts['days']} days" for o in DEC.options}
    fulfill(store, BIZ, "consider/dest", {"summaries": summaries}, by="spark-agent", realization="llm.spark@1")
    out = await here.produce(BIZ, "consider/dest")
    print(f"  back here: outcome={out.outcome} cache_hit={out.cache_hit} · {list(out.node.data['summaries'].values())[9]}")

    print("\nevents:", len(tracker), "·", ", ".join(sorted({e.kind for e in tracker.events()})))
    if store_dir:
        print(f"store at {store_dir} (open it with demos/inspector/server.py --store {store_dir})")


if __name__ == "__main__":
    ap = argparse.ArgumentParser()
    ap.add_argument("--store")
    asyncio.run(main(ap.parse_args().store))
