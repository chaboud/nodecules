"""E6 — is a WASM module's import section a *proof* of coverage?

E2's AST classifier was a best-effort scanner: it found the perturbing calls it
knew about, and it correctly reported that `rgb_to_yuv` reads module constants
the code hash does not cover. Best-effort is the problem — an unknown-unknown
slips through.

A WASM module cannot do that. It has no ambient authority at all: no clock, no
RNG, no environment, no filesystem, no network, unless the host explicitly hands
it an import. So the module's **import section is a complete, machine-checkable
declaration of everything it can reach outside its own bytes** — not a heuristic,
an enforced property of the sandbox.

This file hand-assembles two minimal modules (no toolchain needed) and a tiny
binary-format parser, so the claim can be checked rather than asserted.
"""

from __future__ import annotations

import hashlib
from dataclasses import dataclass

# --- minimal binary encoder ------------------------------------------------


def _uleb(n: int) -> bytes:
    out = bytearray()
    while True:
        b = n & 0x7F
        n >>= 7
        out.append(b | (0x80 if n else 0))
        if not n:
            return bytes(out)


def _name(s: str) -> bytes:
    b = s.encode()
    return _uleb(len(b)) + b


def _section(sid: int, payload: bytes) -> bytes:
    return bytes([sid]) + _uleb(len(payload)) + payload


_HDR = b"\x00asm\x01\x00\x00\x00"
I32 = 0x7F


def pure_module() -> bytes:
    """(module (func (export "add") (param i32 i32) (result i32)
                 local.get 0  local.get 1  i32.add))

    Zero imports. It is *incapable* of observing anything outside its arguments.
    """
    types = _section(1, _uleb(1) + bytes([0x60, 2, I32, I32, 1, I32]))
    funcs = _section(3, _uleb(1) + _uleb(0))
    exports = _section(7, _uleb(1) + _name("add") + bytes([0x00]) + _uleb(0))
    body = bytes([0x00, 0x20, 0x00, 0x20, 0x01, 0x6A, 0x0B])  # locals, get, get, add, end
    code = _section(10, _uleb(1) + _uleb(len(body)) + body)
    return _HDR + types + funcs + exports + code


def perturbing_module() -> bytes:
    """(module (import "env" "now" (func (result i32)))
               (func (export "stamp") (result i32) call 0))

    One import: a host clock. The import section says so, and the module simply
    will not instantiate unless the host supplies it.
    """
    types = _section(1, _uleb(1) + bytes([0x60, 0, 1, I32]))
    imports = _section(2, _uleb(1) + _name("env") + _name("now") + bytes([0x00]) + _uleb(0))
    funcs = _section(3, _uleb(1) + _uleb(0))
    exports = _section(7, _uleb(1) + _name("stamp") + bytes([0x00]) + _uleb(1))
    body = bytes([0x00, 0x10, 0x00, 0x0B])  # locals, call 0, end
    code = _section(10, _uleb(1) + _uleb(len(body)) + body)
    return _HDR + types + imports + funcs + exports + code


# --- minimal binary parser: read the import section ------------------------


@dataclass(frozen=True)
class Import:
    module: str
    name: str
    kind: str

    def __str__(self) -> str:
        return f"{self.module}.{self.name} ({self.kind})"


_KINDS = {0: "func", 1: "table", 2: "memory", 3: "global"}


def read_imports(wasm: bytes) -> list[Import]:
    """Parse just the import section. This is the coverage manifest."""
    assert wasm[:8] == _HDR, "not a wasm module"
    i = 8
    out: list[Import] = []
    while i < len(wasm):
        sid = wasm[i]
        i += 1
        size, i = _read_uleb(wasm, i)
        end = i + size
        if sid == 2:
            count, i = _read_uleb(wasm, i)
            for _ in range(count):
                mod, i = _read_name(wasm, i)
                nm, i = _read_name(wasm, i)
                kind = _KINDS.get(wasm[i], f"?{wasm[i]}")
                i += 1
                _, i = _read_uleb(wasm, i)  # type/limits index
                out.append(Import(mod, nm, kind))
        i = end
    return out


def _read_uleb(b: bytes, i: int) -> tuple[int, int]:
    n = shift = 0
    while True:
        byte = b[i]
        i += 1
        n |= (byte & 0x7F) << shift
        if not byte & 0x80:
            return n, i
        shift += 7


def _read_name(b: bytes, i: int) -> tuple[str, int]:
    ln, i = _read_uleb(b, i)
    return b[i : i + ln].decode(), i + ln


def module_hash(wasm: bytes) -> str:
    """Identity of a portable unit of compute.

    Note what is *absent*: no AST normalization, no source-vs-formatting
    question, no language. The bytes are already canonical, so E1's whole
    source-hash-vs-ast-hash dilemma simply does not arise at this layer.
    """
    return hashlib.sha256(wasm).hexdigest()
