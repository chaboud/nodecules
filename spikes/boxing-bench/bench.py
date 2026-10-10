"""boxing-bench: what does it cost to make every write a transform?

The founder, 2026-10-10: all dependence is acyclic, enforced by only being
able to reach what exists or what a graph can produce; every migration takes
one state of data to the next by a transform graph, primitives like "add an
audio buffer to a strip" included; and it is made fast by boxing, as C#
boxes a value type when it has to be treated as an object.

Two experiments against the shipped core (no changes to it):

  B1  Does a commit refuse an edge to a missing name, or a cycle?
  B2  An append done as a plain function (unboxed: `strip_append`) against
      the same append done as a full production (boxed: each new strip
      version is a produced node reading the previous version and one
      element node). Cost per append as the chain grows, and where it goes.

Run:  cd spikes/boxing-bench && python3 bench.py
"""
from __future__ import annotations

import asyncio
import cProfile
import pstats
import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[2] / "backend"))

from nodecules.core.generation import RECIPE_ROLE, RECIPE_TEMPLATE_KIND, Generator, Realization  # noqa: E402
from nodecules.core.store import Cycle, Edge, Node, Store  # noqa: E402
from nodecules.core.strip_nodes import strip_append, strip_read  # noqa: E402


def b1() -> None:
    print("B1  what a commit accepts")
    s = Store()
    tx = s.transaction("w", author="probe")
    tx.put(Node(id="a", kind="k", scope="w", data={}, edges=(Edge(role="in", target="b"),)))
    m = tx.commit("a reads b; b does not exist")
    print(f"    edge to a missing name: accepted (manifest seq {m.seq})")
    tx = s.transaction("w", author="probe")
    tx.put(Node(id="b", kind="k", scope="w", data={}, edges=(Edge(role="in", target="a"),)))
    m = tx.commit("b reads a; closes a cycle in one snapshot")
    print(f"    cycle in one snapshot:  accepted (manifest seq {m.seq})")
    try:
        s.composed_hash(s.snapshot(), "w", "a")
        print("    walk: no error (unexpected)")
    except Cycle as e:
        print(f"    found only when walked: Cycle({e})")


async def append_cook(inputs, params):
    prev = inputs.get("prev")
    return {"elements": [*(prev["elements"] if prev else []), inputs["el"]]}


def unboxed(n: int):
    s = Store()
    t = time.perf_counter()
    for k in range(n):
        strip_append(s, "w", "strips/a", {"k": k}, author="bench")
    return time.perf_counter() - t, len(strip_read(s, s.current("w"), "strips/a"))


async def boxed(n: int, gen_holder=None):
    s = Store()
    tx = s.transaction("lib", author="bench")
    tx.put(Node(id="r/append", kind=RECIPE_TEMPLATE_KIND, scope="lib", data={"realization": "strip.append@1", "params": {}}))
    tx.commit("recipe")
    g = Generator(s, [Realization("strip.append@1", append_cook, deterministic=True)], author="bench")
    gen = None
    t = time.perf_counter()
    for k in range(n):
        tx = s.transaction("w", author="bench")
        tx.put(Node(id=f"in/{k}", kind="element", scope="w", data={"k": k}))
        edges = [Edge(target=f"in/{k}", role="el"), Edge(target="r/append", scope="lib", role=RECIPE_ROLE)]
        if k:
            edges.append(Edge(target=f"strips/b/v{k - 1}", role="prev"))
        tx.put(Node(id=f"strips/b/v{k}", kind="strip", scope="w", edges=tuple(edges)))
        tx.commit(f"declare v{k}")
        gen = await g.produce("w", f"strips/b/v{k}")
    return time.perf_counter() - t, len(gen.node.data["elements"])


def b2(sizes) -> None:
    print("B2  unboxed append against boxed append (wall time, this machine)")
    for n in sizes:
        u, ul = unboxed(n)
        b, bl = asyncio.run(boxed(n))
        assert ul == bl == n
        print(f"    n={n:4d}  unboxed {u / n * 1e6:7.0f} us/append   boxed {b / n * 1e6:7.0f} us/append   ratio {b / u:6.1f}x")
    n = 150
    prof = cProfile.Profile()
    prof.enable()
    asyncio.run(boxed(n))
    prof.disable()
    stats = pstats.Stats(prof)
    calls = next(v[1] for k, v in stats.stats.items() if k[2] == "_produce_one")
    print(f"    where it goes, n={n}: {calls} single-node productions for {n} appends "
          f"(n(n+1) + n = {n * (n + 1) + n}): produce k revisits every earlier version, "
          f"its element, and the recipe")


if __name__ == "__main__":
    b1()
    b2([10, 100, 300])
