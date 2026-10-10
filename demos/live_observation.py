"""Live observation with relative reads: the first piece of a toy keyhole.

A camera appends what it sees to a strip, one observation per tick. Nobody
calls produce. Three nodes stay fresh by themselves:

  now     what is in view: the observations in the last three seconds,
          measured back from the newest one (a head-relative window, so no
          clock is read anywhere)
  views   (a strip) each "now", appended by a recorder: the generational
          boundary that lets the next node compare now with before without
          a node reading its own output
  delta   what appeared and what left: the last two views, by count

A reactor keeps `now` fresh; a second keeps `delta` fresh off the views
strip. Then a janitor releases old observations, and a node that reads the
whole history comes back lost, naming the floor, while the windowed reads
carry on.

Does not show: a real camera or model (observations are scripted, the
summaries are rules), wall-clock ticks (time is the observations' own),
the fast, slow, and inner loops of the real keyhole.

    cd backend && PYTHONPATH=. python3 ../demos/live_observation.py
"""

from __future__ import annotations

import asyncio
import os
import sys

sys.path.insert(0, os.path.join(os.path.dirname(__file__)))

from nodecules.core.generation import RECIPE_ROLE, RECIPE_TEMPLATE_KIND, Generator, Realization  # noqa: E402
from nodecules.core.reactor import Reactor  # noqa: E402
from nodecules.core.store import Edge, Node, Store, envelope_id  # noqa: E402
from nodecules.core.strip_access import Ago, AllPattern, RangePattern, last  # noqa: E402
from nodecules.core.strip_nodes import strip_append, strip_trim  # noqa: E402

ROOM, LIB = "room", "lib"
SCRIPT = [  # tick -> what the camera sees appear in that second
    (0, ["person"]),
    (1, ["cup"]),
    (2, ["person", "cup"]),
    (3, ["cup"]),
    (4, ["dog"]),
    (5, ["dog", "ball"]),
    (6, []),
    (7, ["person"]),
    (8, ["person", "dog"]),
]


async def in_view(inputs, params):
    seen = sorted({o["what"] for o in inputs["recent"]} - {"nothing"})
    return {"in_view": seen}


async def delta(inputs, params):
    views = inputs["views"]
    if len(views) < 2:
        return {"appeared": views[-1]["in_view"] if views else [], "left": []}
    before, now = set(views[-2]["in_view"]), set(views[-1]["in_view"])
    return {"appeared": sorted(now - before), "left": sorted(before - now)}


async def history(inputs, params):
    return {"observations": len(inputs["all"])}


def declare(store: Store) -> None:
    tx = store.transaction(LIB, author="dev")
    for rid, handle in (("r/in_view", "in_view@1"), ("r/delta", "delta@1"), ("r/history", "history@1")):
        tx.put(Node(id=rid, kind=RECIPE_TEMPLATE_KIND, scope=LIB, data={"realization": handle, "params": {}}))
    tx.commit("recipes")
    recent = RangePattern(field="ts", start=Ago(anchor="head", ms=3000), end=Ago(anchor="head"))
    tx = store.transaction(ROOM, author="dev")
    tx.put(Node(id="now", kind="view", scope=ROOM, edges=(Edge(target="strips/seen", pattern=recent, role="recent"), Edge(target="r/in_view", scope=LIB, role=RECIPE_ROLE))))
    tx.put(Node(id="delta", kind="change", scope=ROOM, edges=(Edge(target="strips/views", pattern=last(2), role="views"), Edge(target="r/delta", scope=LIB, role=RECIPE_ROLE))))
    tx.put(Node(id="history", kind="count", scope=ROOM, edges=(Edge(target="strips/seen", pattern=AllPattern(), role="all"), Edge(target="r/history", scope=LIB, role=RECIPE_ROLE))))
    tx.commit("the room's graph: now, delta, history")


async def main() -> None:
    store = Store()
    declare(store)
    realizations = [Realization("in_view@1", in_view, deterministic=True), Realization("delta@1", delta, deterministic=True), Realization("history@1", history, deterministic=True)]

    def record(result):
        strip_append(store, ROOM, "strips/views", result[(ROOM, "now")].node.data, author="recorder")

    watching = Reactor(Generator(store, realizations, author="watcher"), [(ROOM, "now"), (ROOM, "history")], on_result=record)
    noticing = Reactor(Generator(store, realizations, author="noticer"), [(ROOM, "delta")])
    for tick, things in SCRIPT:
        t = tick * 1000
        strip_append(store, ROOM, "strips/seen", *[{"ts": {"start_ms": t, "end_ms": t + 1000}, "what": w} for w in things] or [{"ts": {"start_ms": t, "end_ms": t + 1000}, "what": "nothing"}], author="camera")
        if tick == 0:
            watching.start()
            noticing.start()
        await watching.settle()
        await noticing.settle()
        m = store.current(ROOM)
        now = store.get(m, "now").data["in_view"]
        d = store.get(m, "delta").data
        print(f"t={tick}s  in view: {', '.join(now) or '(nothing)':22}  appeared: {', '.join(d['appeared']) or '-':14} left: {', '.join(d['left']) or '-'}")

    env = store.get(store.current(ROOM), envelope_id("now"))
    print(f"\nthe receipt for `now` says what its relative window resolved to: {env.data['recipe']['reads']}")
    print(f"`history` (an All read) has seen {store.get(store.current(ROOM), 'history').data['observations']} observations")

    print("\na janitor keeps the last six observations")
    strip_trim(store, ROOM, "strips/seen", keep_last=6, author="janitor")
    strip_append(store, ROOM, "strips/seen", {"ts": {"start_ms": 9000, "end_ms": 10000}, "what": "cup"}, author="camera")
    await watching.settle()
    await noticing.settle()
    print(f"  now:     {watching.last[(ROOM, 'now')].outcome}, in view: {watching.last[(ROOM, 'now')].node.data['in_view']}")
    h = watching.last[(ROOM, "history")]
    print(f"  history: {h.outcome} — {h.note}")
    print(f"\nreactor runs: watching {watching.runs}, noticing {noticing.runs}; nobody called produce")


if __name__ == "__main__":
    asyncio.run(main())
