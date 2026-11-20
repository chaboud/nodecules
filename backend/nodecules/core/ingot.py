"""Portable units of compute: a WASM ingot as a realization (vault ADR-0014).

An *ingot* is the reference realization of a job, compiled to WebAssembly:
portable to any machine with a WASM runtime, and the conformance oracle for
its *castings* (faster, hardware-specific realizations of the same job).

What makes WASM the right ingot is measured in `spikes/identity-bench/` E6:
a module has no ambient authority, so its import section is a complete,
enforced declaration of everything it can reach outside its own bytes. Here
that becomes structure:

- `read_imports` parses the import section from the bytes, with no runtime,
  so anyone can check the proof.
- `ingot_realization` refuses a module whose imports are not all granted,
  before anything runs. A module with no imports is deterministic by proof
  (it can observe nothing but its arguments); a module with any granted
  import is not, and says so (default to perturbing).
- `cast_check` runs a casting against its ingot on probes and reports the
  largest difference and every mismatch. It certifies the probed subset and
  nothing more (ADR-0021): a casting wrong only off the probes passes, which
  is why probe provenance belongs on the receipt.

The runtime (`wasmtime`) is optional and loaded only when a cook runs; the
core imports without it. This first form is the i32 ABI only: the export
takes integer arguments named by the recipe's `args` param and returns one
integer. Memory, strings, and buffers come later.
"""

from __future__ import annotations

import asyncio
import base64
import hashlib
from dataclasses import dataclass
from typing import Any, Callable, Dict, Iterable, List, Mapping, Optional, Sequence, Tuple

from .generation import Realization
from .store import Node

INGOT_KIND = "ingot"


class IngotRefused(Exception):
    """The module reaches something it was not granted. Nothing ran."""


@dataclass(frozen=True)
class Import:
    module: str
    name: str
    kind: int  # 0 func, 1 table, 2 memory, 3 global

    def __str__(self) -> str:
        return f"{self.module}.{self.name}"


def _read_uleb(b: bytes, i: int) -> Tuple[int, int]:
    result = shift = 0
    while True:
        byte = b[i]
        i += 1
        result |= (byte & 0x7F) << shift
        if not byte & 0x80:
            return result, i
        shift += 7


def _read_name(b: bytes, i: int) -> Tuple[str, int]:
    n, i = _read_uleb(b, i)
    return b[i : i + n].decode(), i + n


def read_imports(wasm: bytes) -> List[Import]:
    """The import section: everything the module can reach outside itself."""
    if wasm[:8] != b"\x00asm\x01\x00\x00\x00":
        raise ValueError("not a WebAssembly 1.0 module")
    i = 8
    out: List[Import] = []
    while i < len(wasm):
        sid = wasm[i]
        size, i = _read_uleb(wasm, i + 1)
        end = i + size
        if sid == 2:
            count, j = _read_uleb(wasm, i)
            for _ in range(count):
                mod, j = _read_name(wasm, j)
                name, j = _read_name(wasm, j)
                kind = wasm[j]
                j += 1
                if kind == 0:  # func: type index
                    _, j = _read_uleb(wasm, j)
                elif kind == 1:  # table: reftype + limits
                    j += 1
                    flag = wasm[j]
                    j += 1
                    _, j = _read_uleb(wasm, j)
                    if flag & 1:
                        _, j = _read_uleb(wasm, j)
                elif kind == 2:  # memory: limits
                    flag = wasm[j]
                    j += 1
                    _, j = _read_uleb(wasm, j)
                    if flag & 1:
                        _, j = _read_uleb(wasm, j)
                elif kind == 3:  # global: valtype + mutability
                    j += 2
                out.append(Import(mod, name, kind))
        i = end
    return out


def module_hash(wasm: bytes) -> str:
    return hashlib.sha256(wasm).hexdigest()


def ingot_node(scope: str, name: str, wasm: bytes, export: str) -> Node:
    """The ingot as a node: its bytes, the export it offers, and the imports
    its bytes declare. Content-addressed, so one changed byte is a different
    ingot."""
    return Node(
        id=name,
        kind=INGOT_KIND,
        scope=scope,
        data={
            "wasm": base64.b64encode(wasm).decode(),
            "export": export,
            "imports": [str(x) for x in read_imports(wasm)],
            "module_hash": module_hash(wasm),
        },
    )


def runtime_available() -> bool:
    try:
        import wasmtime  # noqa: F401
    except ImportError:
        return False
    return True


def ingot_realization(
    handle: str,
    wasm: bytes,
    export: str,
    *,
    grants: Optional[Mapping[str, Callable[..., int]]] = None,
) -> Realization:
    """A realization that runs `export` from the module. Refused here, before
    anything runs, if the module imports anything `grants` does not supply
    (keys are "module.name"). Deterministic only when it imports nothing."""
    grants = dict(grants or {})
    imports = read_imports(wasm)
    ungranted = [str(x) for x in imports if str(x) not in grants]
    if ungranted:
        raise IngotRefused(f"{handle}: the module imports {ungranted}, which nothing granted; it cannot run here")

    async def cook(inputs: Dict[str, Any], params: Dict[str, Any]) -> Any:
        args = [int(inputs[role]) for role in params.get("args", sorted(inputs))]
        return await asyncio.to_thread(_call, wasm, export, args, imports, grants)

    return Realization(handle=handle, cook=cook, deterministic=not imports)


def _call(wasm: bytes, export: str, args: Sequence[int], imports: Sequence[Import], grants: Mapping[str, Callable[..., int]]) -> int:
    try:
        import wasmtime
    except ImportError:
        raise RuntimeError("no WASM runtime on this machine (pip install wasmtime)") from None
    engine = wasmtime.Engine()
    store = wasmtime.Store(engine)
    module = wasmtime.Module(engine, wasm)
    linker = wasmtime.Linker(engine)
    for imp, ty in zip(imports, module.imports):
        fn = grants[str(imp)]
        linker.define(store, imp.module, imp.name, wasmtime.Func(store, ty.type, fn))
    instance = linker.instantiate(store, module)
    return int(instance.exports(store)[export](store, *args))


@dataclass(frozen=True)
class CastCheck:
    """A casting run against its ingot on probes. `max_abs` is the largest
    difference seen; `passed` says it is within `tolerance`. It certifies
    the probed inputs and nothing else."""

    reference: str
    casting: str
    probes: int
    probe_provenance: str
    max_abs: float
    tolerance: float
    mismatches: Tuple[Tuple[Dict[str, Any], Any, Any], ...]

    @property
    def passed(self) -> bool:
        return self.max_abs <= self.tolerance


def cast_check(
    ingot: Realization,
    casting: Realization,
    probes: Iterable[Mapping[str, Any]],
    *,
    params: Optional[Mapping[str, Any]] = None,
    tolerance: float = 0.0,
    probe_provenance: str = "fixed-suite",
) -> CastCheck:
    """Run both on every probe and compare."""
    params = dict(params or {})
    probes = [dict(p) for p in probes]

    async def run() -> List[Tuple[Any, Any]]:
        return [(await ingot.cook(dict(p), params), await casting.cook(dict(p), params)) for p in probes]

    results = asyncio.run(run())
    worst = 0.0
    bad: List[Tuple[Dict[str, Any], Any, Any]] = []
    for p, (want, got) in zip(probes, results):
        d = abs(float(got) - float(want))
        worst = max(worst, d)
        if d > tolerance:
            bad.append((p, want, got))
    return CastCheck(ingot.handle, casting.handle, len(probes), probe_provenance, worst, tolerance, tuple(bad))


__all__ = [
    "CastCheck",
    "INGOT_KIND",
    "Import",
    "IngotRefused",
    "cast_check",
    "ingot_node",
    "ingot_realization",
    "module_hash",
    "read_imports",
    "runtime_available",
]
