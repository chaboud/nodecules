"""A WASM ingot as a realization (vault ADR-0014; spikes/identity-bench E6).
The import section is checked before anything runs; a module with no
imports is deterministic by proof; a casting is checked against its ingot
on probes, and that certifies the probes and nothing more. The runtime is
optional: these tests skip without it, and the core imports either way."""

from __future__ import annotations

import asyncio
import random

import pytest

from nodecules.core.generation import RECIPE_ROLE, RECIPE_TEMPLATE_KIND, Generator, Realization
from nodecules.core.ingot import IngotRefused, cast_check, ingot_node, ingot_realization, read_imports, runtime_available
from nodecules.core.store import Edge, Node, Store

wasmtime = pytest.importorskip("wasmtime")

ADD = wasmtime.wat2wasm('(module (func (export "add") (param i32 i32) (result i32) local.get 0 local.get 1 i32.add))')
STAMP = wasmtime.wat2wasm('(module (import "env" "now" (func $now (result i32))) (func (export "stamp") (param i32) (result i32) local.get 0 call $now i32.add))')
S, LIB = "calc", "lib"


def test_the_import_section_is_read_from_the_bytes_and_ungranted_reach_is_refused_before_running():
    assert read_imports(ADD) == []
    assert [str(i) for i in read_imports(STAMP)] == ["env.now"]
    with pytest.raises(IngotRefused, match="env.now"):
        ingot_realization("stamp@1", STAMP, "stamp")
    granted = ingot_realization("stamp@1", STAMP, "stamp", grants={"env.now": lambda: 1000})
    assert granted.deterministic is False  # it can read a clock: perturbing, by its own declaration
    assert ingot_realization("add.wasm@1", ADD, "add").deterministic is True  # it can observe nothing but its arguments
    assert asyncio.run(granted.cook({"x": 5}, {"args": ["x"]})) == 1005


def test_an_ingot_is_a_content_addressed_node_and_one_byte_makes_another():
    a = ingot_node(LIB, "ingots/add", ADD, "add")
    tweaked = bytearray(ADD)
    tweaked[-2] ^= 0x01  # inside the code body
    b = ingot_node(LIB, "ingots/add", bytes(tweaked), "add")
    assert a.data["imports"] == [] and a.data["module_hash"] != b.data["module_hash"]
    assert a.content_hash() != b.content_hash()


def _graph(store: Store, handle: str) -> None:
    tx = store.transaction(LIB, author="dev")
    tx.put(Node(id="r/add", kind=RECIPE_TEMPLATE_KIND, scope=LIB, data={"realization": handle, "params": {"args": ["a", "b"]}}))
    tx.commit()
    tx = store.transaction(S, author="dev")
    tx.put(Node(id="a", kind="int", scope=S, data=2))
    tx.put(Node(id="b", kind="int", scope=S, data=3))
    tx.put(Node(id="sum", kind="int", scope=S, edges=(Edge(target="a", role="a"), Edge(target="b", role="b"), Edge(target="r/add", scope=LIB, role=RECIPE_ROLE))))
    tx.commit()


async def _py_add(inputs, params):
    return int(inputs["a"]) + int(inputs["b"])


def test_the_ingot_runs_through_the_generator_and_a_casting_substitutes_visibly():
    store = Store()
    _graph(store, "add.wasm@1")
    ingot = ingot_realization("add.wasm@1", ADD, "add")
    casting = Realization("add.py@1", _py_add, deterministic=True)
    g = asyncio.run(Generator(store, [ingot, casting], author="gen").produce(S, "sum"))
    assert g.node.data == 5 and g.outcome == "exact" and g.realization == "add.wasm@1" and g.reproducibility == "exact"
    again = asyncio.run(Generator(store, [ingot, casting], author="gen").produce(S, "sum"))
    assert again.cache_hit
    store2 = Store()
    _graph(store2, "add.wasm@1")
    sub = asyncio.run(Generator(store2, [ingot, casting], bindings={"sum": "add.py@1"}, author="gen").produce(S, "sum"))
    assert sub.node.data == 5 and sub.outcome == "via-substitute" and sub.declared_realization == "add.wasm@1"


def test_a_casting_is_checked_against_its_ingot_and_the_check_certifies_only_what_it_probed():
    ingot = ingot_realization("add.wasm@1", ADD, "add")
    good = Realization("add.py@1", _py_add, deterministic=True)

    async def wrong_when_big(inputs, params):
        a, b = int(inputs["a"]), int(inputs["b"])
        return a + b + (1 if a > 1000 else 0)  # wrong on a region the small probes never visit

    bad = Realization("add.fast@1", wrong_when_big, deterministic=True)
    small = [{"a": a, "b": b} for a in range(0, 50, 7) for b in range(0, 50, 11)]
    rng = random.Random(7)
    wide = [{"a": rng.randrange(0, 5000), "b": rng.randrange(0, 5000)} for _ in range(60)]
    params = {"args": ["a", "b"]}
    assert cast_check(ingot, good, wide, params=params, probe_provenance="fresh-drawn").passed
    blind = cast_check(ingot, bad, small, params=params)
    assert blind.passed and blind.probes == len(small)  # passes: the bug is off the probes (ADR-0021)
    caught = cast_check(ingot, bad, wide, params=params, probe_provenance="fresh-drawn")
    assert not caught.passed and caught.max_abs == 1.0 and all(p["a"] > 1000 for p, _, _ in caught.mismatches)
