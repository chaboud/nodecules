"""Content-addressed node identity + composed graph hashes.

Spike, not core. Tests the load-bearing half of ADR-0003: can a node's identity
come from a hash of its *code* rather than a declared version string, and does
composing those hashes give a usable graph identity?

Two identity strategies are implemented so we can measure the difference:

  source  — sha256 of the function's source text. Comments and formatting
            change the hash.
  ast     — sha256 of the normalized AST dump. Comments and formatting do not
            change the hash; docstrings still do (they are AST nodes).

Which is correct is a real design question, not a detail: `source` over-
invalidates (a comment edit throws away every cached result), `ast` under-
invalidates in exactly one way we can name (a docstring is behaviour for an
LLM-facing node, and `ast` keeps it, so that case is actually fine — the real
gap is closure state and module-level constants, which neither captures).
"""

from __future__ import annotations

import ast
import hashlib
import inspect
import json
import textwrap
from dataclasses import dataclass
from typing import Any, Callable


def _sha(data: str) -> str:
    return hashlib.sha256(data.encode("utf-8")).hexdigest()


def _canonical(obj: Any) -> str:
    return json.dumps(obj, sort_keys=True, separators=(",", ":"), default=repr)


def source_hash(fn: Callable) -> str:
    """Hash the literal source text. Comments and whitespace count."""
    return _sha(textwrap.dedent(inspect.getsource(fn)))


def ast_hash(fn: Callable) -> str:
    """Hash the normalized AST. Comments and formatting do not count.

    Line/column attributes are excluded (`include_attributes=False`) so that
    moving a function within a file does not change its identity.
    """
    tree = ast.parse(textwrap.dedent(inspect.getsource(fn)))
    return _sha(ast.dump(tree, include_attributes=False))


def code_hash(fn: Callable, strategy: str = "ast") -> str:
    return ast_hash(fn) if strategy == "ast" else source_hash(fn)


@dataclass(frozen=True)
class NodeRef:
    """A node's identity: what code, at what declared identity strategy."""

    name: str
    code_digest: str
    identity_kind: str  # "pure" | "perturbing" | "declared-equivalent"

    def short(self) -> str:
        return f"{self.name}@{self.code_digest[:12]}"


def node_ref(fn: Callable, identity_kind: str = "pure", strategy: str = "ast") -> NodeRef:
    return NodeRef(
        name=fn.__name__,
        code_digest=code_hash(fn, strategy),
        identity_kind=identity_kind,
    )


# --- Composed graph hash ---------------------------------------------------


@dataclass(frozen=True)
class GraphNode:
    ref: NodeRef
    params: tuple  # canonicalized (key, value) pairs
    inputs: tuple  # tuple of upstream node hashes, ORDER SIGNIFICANT


def node_hash(gn: GraphNode) -> str:
    """Hash one node in position: its code, its knobs, and what it read.

    ADR-0003's lower layer. Input order is part of the digest — port order is
    part of a node's observable interface (nodecules feat/temporality made the
    same call independently).
    """
    payload = {
        "code": gn.ref.code_digest,
        "kind": gn.ref.identity_kind,
        "params": list(gn.params),
        "inputs": list(gn.inputs),
    }
    return _sha(_canonical(payload))


def composed_hash(node_hashes: list[str]) -> str:
    """Merkle roll-up over a linear chain, root last.

    Deliberately a *tree* fold rather than a flat digest of the set: a change
    to a leaf changes only the path to the root, so sibling subtrees keep their
    identity (ADR-0012's surviving claim from the superseded ADR-0008).
    """
    acc = ""
    for h in node_hashes:
        acc = _sha(acc + h)
    return acc


def build_chain(fns: list[Callable], kinds: list[str] | None = None,
                strategy: str = "ast") -> tuple[str, list[str]]:
    """Build a linear pipeline and return (composed_hash, per_node_hashes)."""
    kinds = kinds or ["pure"] * len(fns)
    hashes: list[str] = []
    prev: tuple = ()
    for fn, kind in zip(fns, kinds):
        gn = GraphNode(ref=node_ref(fn, kind, strategy), params=(), inputs=prev)
        h = node_hash(gn)
        hashes.append(h)
        prev = (h,)
    return composed_hash(hashes), hashes
