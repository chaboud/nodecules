"""The core imports without a WASM runtime, the import-section proof can be
checked without one, and a cook that needs one fails as a state, saying
what is missing. Runs with `wasmtime` blocked, whether or not it is installed."""

from __future__ import annotations

import os
import subprocess
import sys
from pathlib import Path

ADD_HEX = "0061736d0100000001070160027f7f017f030201000707010361646400000a09010700200020016a0b"

SCRIPT = f"""
import asyncio, sys
sys.modules["wasmtime"] = None  # as if not installed
from nodecules.core.ingot import ingot_realization, read_imports, runtime_available
from nodecules.core.generation import Generator, RECIPE_ROLE, RECIPE_TEMPLATE_KIND
from nodecules.core.store import Edge, Node, Store
wasm = bytes.fromhex("{ADD_HEX}")
assert read_imports(wasm) == [] and runtime_available() is False
r = ingot_realization("add.wasm@1", wasm, "add")
store = Store()
tx = store.transaction("lib"); tx.put(Node(id="r", kind=RECIPE_TEMPLATE_KIND, scope="lib", data={{"realization": "add.wasm@1", "params": {{"args": ["a", "b"]}}}})); tx.commit()
tx = store.transaction("s"); tx.put(Node(id="a", kind="int", scope="s", data=2)); tx.put(Node(id="b", kind="int", scope="s", data=3))
tx.put(Node(id="sum", kind="int", scope="s", edges=(Edge(target="a", role="a"), Edge(target="b", role="b"), Edge(target="r", scope="lib", role=RECIPE_ROLE)))); tx.commit()
g = asyncio.run(Generator(store, [r]).produce("s", "sum"))
assert g.outcome == "failed" and "no WASM runtime" in g.error, g
print("ok")
"""


def test_without_a_runtime_the_proof_still_checks_and_a_cook_fails_saying_why():
    here = Path(__file__).resolve().parents[2]
    out = subprocess.run([sys.executable, "-c", SCRIPT], capture_output=True, text=True, timeout=60, env={**os.environ, "PYTHONPATH": str(here)})
    assert out.returncode == 0, out.stdout + out.stderr
    assert out.stdout.strip() == "ok"
