"""A toy keyhole: a desk camera on a dock that is plugged and unplugged.

The founder's case (2026-10-10): "I have a dock at my desk that has a camera
connected. I connect and disconnect." Every connection is a segment strip
with its own clock; the camera's long strip is produced over the segments
with coverage (vault ADR-0039). Nobody calls produce: a reactor keeps two
readers fresh as the camera writes.

  now         what is in view: the last three seconds of what was watched,
              promise "nearest" (vault ADR-0038). While the camera is away it
              still answers, marked provisional (wet ink), with what it owes.
  now/strict  the same window, promise "exactly": it misses instead, and says
              why (not-yet while away, gap when the window reaches into the
              dead band).
  glance      what was in view at one moment in the past, exactly: a gap
              until the camera's buffered footage is backfilled.

Does not show: a real camera or model (observations are scripted), wall
time (the clock is the observations' own), keyhole's fast, slow, and inner
loops. Keyhole itself is untouched; this is nodecules exercising the
substrate keyhole would sit on.

    cd backend && PYTHONPATH=. python3 ../demos/toy_keyhole.py
"""

from __future__ import annotations

import asyncio
import os
import sys

sys.path.insert(0, os.path.join(os.path.dirname(__file__)))

from nodecules.core.generation import PARAMS_ROLE, RECIPE_ROLE, RECIPE_TEMPLATE_KIND, Generator, Realization  # noqa: E402
from nodecules.core.reactor import Reactor  # noqa: E402
from nodecules.core.segments import append, backfill, disconnect, merge_realization, open_segment  # noqa: E402
from nodecules.core.store import Edge, Node, Store  # noqa: E402
from nodecules.core.strip_access import Ago, AtTimePattern, RangePattern, SelfWindowStart  # noqa: E402

DESK, LIB = "desk", "lib"
CAM = "sources/desk-cam"


def seen(t: int, *what: str) -> list:
    return [{"ts": {"start_ms": t, "end_ms": t + 1000}, "what": w} for w in what]


async def in_view(inputs, params):
    return {"in_view": sorted({e["what"] for e in inputs["recent"] or []})}


async def glance(inputs, params):
    e = inputs["moment"]
    return {"saw": e["what"] if e else None}


def declare(store: Store) -> None:
    tx = store.transaction(LIB, author="dev")
    tx.put(Node(id="r/in_view", kind=RECIPE_TEMPLATE_KIND, scope=LIB, data={"realization": "in_view@1", "params": {}}))
    tx.put(Node(id="r/glance", kind=RECIPE_TEMPLATE_KIND, scope=LIB, data={"realization": "glance@1", "params": {}}))
    tx.commit("recipes")
    last3 = dict(field="ts", start=Ago(anchor="head", ms=3000), end=Ago(anchor="head"))
    tx = store.transaction(DESK, author="dev")
    tx.put(Node(id="now", kind="view", scope=DESK, edges=(Edge(target=CAM, pattern=RangePattern(**last3, promise="nearest"), role="recent"), Edge(target="r/in_view", scope=LIB, role=RECIPE_ROLE))))
    tx.put(Node(id="now/strict", kind="view", scope=DESK, edges=(Edge(target=CAM, pattern=RangePattern(**last3), role="recent"), Edge(target="r/in_view", scope=LIB, role=RECIPE_ROLE))))
    tx.put(Node(id="params/glance", kind="params", scope=DESK, data={"window": {"start_ms": 300_500, "end_ms": 300_501}}))
    tx.put(Node(id="glance", kind="glance", scope=DESK, edges=(Edge(target=CAM, pattern=AtTimePattern(field="ts", time=SelfWindowStart()), role="moment"), Edge(target="r/glance", scope=LIB, role=RECIPE_ROLE), Edge(target="params/glance", role=PARAMS_ROLE))))
    tx.commit("the desk's graph")


def show(label: str, reactor: Reactor) -> None:
    now, strict = reactor.last.get((DESK, "now")), reactor.last.get((DESK, "now/strict"))
    if now is None or now.node is None:
        print(f"{label:34} now: {now.outcome if now else '-'}")
        return
    ink = f"wet: owes {', '.join(now.owed)}" if now.provisional else "dry"
    strict_says = "hit" if strict and strict.outcome in ("exact", "via-substitute") else f"{strict.outcome} ({strict.reason})"
    print(f"{label:34} now: {', '.join(now.node.data['in_view']) or '(nothing)':14} [{ink}]   strict: {strict_says}")


async def main() -> None:
    store = Store()
    declare(store)
    realizations = [merge_realization(), Realization("in_view@1", in_view, deterministic=True), Realization("glance@1", glance, deterministic=True)]
    watching = Reactor(Generator(store, realizations, author="watcher"), [(DESK, "now"), (DESK, "now/strict")])

    print("the dock connects; the camera's clock starts at 0")
    open_segment(store, DESK, CAM, at_ms=0, author="dock")
    for tick, things in enumerate([("cup",), ("cup", "person"), ("person",), ("person", "phone"), ("phone",)]):
        append(store, DESK, CAM, *seen(tick * 1000, *things), author="camera")
        if tick == 0:
            watching.start()
        await watching.settle()
        show(f"  t={tick}s", watching)

    print("\nunplugged at 5 s (the operating system says so: a fact)")
    disconnect(store, DESK, CAM, at_ms=5000, author="dock")
    await watching.settle()
    show("  away", watching)

    print("\nplugged back in ten minutes later; the camera's clock restarted at 0")
    open_segment(store, DESK, CAM, at_ms=0, offset_ms=600_000, author="dock")
    for tick, things in enumerate([("dog",), ("dog", "ball"), ("ball",), ("ball",)]):
        append(store, DESK, CAM, *seen(tick * 1000, *things), author="camera")
        await watching.settle()
        show(f"  t=600+{tick}s", watching)

    m = store.current(DESK)
    print(f"\nthe long strip's coverage: {store.get(m, CAM).data['coverage']}")
    gen = Generator(store, realizations, author="asker")
    g = await gen.produce(DESK, "glance")
    print(f"glance at 300.5 s, exactly: {g.outcome} ({g.reason}): {g.note}")

    print("\nthe camera uploads what it buffered while unplugged (backfill: one more segment)")
    backfill(store, DESK, CAM, *seen(300_000, "cat"), from_ms=5000, to_ms=600_000, author="camera")
    g = await gen.produce(DESK, "glance")
    print(f"glance at 300.5 s, exactly: {g.outcome}: saw {g.node.data['saw']}")
    print(f"the long strip's coverage now: {store.get(store.current(DESK), CAM).data['coverage']}")
    print(f"\nreactor runs: {watching.runs}; nobody called produce for `now`")
    watching.stop()


if __name__ == "__main__":
    asyncio.run(main())
