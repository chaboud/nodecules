"""Static perturbation classifier — ADR-0009, and P-25's coverage question.

Tests the claim that "most pseudo-random and raw-source data can be clearly
noted". If a classifier can find perturbing nodes automatically, the
default-to-perturbing rule is cheap. If it can't, every node author has to
declare by hand and the rule will be honoured about as well as nodecules'
ResourceRequirement was.

Precedent: PewPew's scripts/check-deps.mjs enforces exactly this discipline
for src/sim/ (no DOM, no Math.random, no Date.now) and it holds.
"""

from __future__ import annotations

import ast
import inspect
import textwrap
from dataclasses import dataclass, field

# name -> why it perturbs
_PERTURBING_CALLS = {
    "time.time": "clock",
    "time.monotonic": "clock",
    "time.perf_counter": "clock",
    "datetime.now": "clock",
    "datetime.utcnow": "clock",
    "random.random": "unseeded rng",
    "random.randint": "unseeded rng",
    "random.choice": "unseeded rng",
    "random.uniform": "unseeded rng",
    "os.environ.get": "environment read",
    "os.getenv": "environment read",
    "os.urandom": "entropy source",
    "open": "file i/o",
    "requests.get": "network",
    "socket.socket": "network",
    "uuid.uuid4": "entropy source",
}

# constructors that are seeded from a declared argument are NOT perturbing
_SEEDED_CONSTRUCTORS = {"random.Random"}


@dataclass
class Finding:
    kind: str
    detail: str
    lineno: int


@dataclass
class Verdict:
    name: str
    identity_kind: str  # "pure" | "perturbing"
    findings: list[Finding] = field(default_factory=list)
    coverage_gaps: list[str] = field(default_factory=list)

    def line(self) -> str:
        if self.identity_kind == "pure":
            gap = f"  (uncovered: {', '.join(self.coverage_gaps)})" if self.coverage_gaps else ""
            return f"  pure        {self.name}{gap}"
        why = ", ".join(sorted({f.kind for f in self.findings}))
        return f"  PERTURBING  {self.name}  <- {why}"


def _dotted(node: ast.AST) -> str:
    """Reconstruct a dotted call name from an AST node, best effort."""
    parts = []
    while isinstance(node, ast.Attribute):
        parts.append(node.attr)
        node = node.value
    if isinstance(node, ast.Name):
        parts.append(node.id)
    return ".".join(reversed(parts))


class _Walker(ast.NodeVisitor):
    def __init__(self) -> None:
        self.findings: list[Finding] = []
        self.reads_free_names: set[str] = set()
        self._bound: set[str] = set()
        self._seeded_ok = False

    def visit_arg(self, node: ast.arg) -> None:
        self._bound.add(node.arg)

    def visit_Assign(self, node: ast.Assign) -> None:
        for t in ast.walk(node):
            if isinstance(t, ast.Name) and isinstance(t.ctx, ast.Store):
                self._bound.add(t.id)
        self.generic_visit(node)

    def visit_Global(self, node: ast.Global) -> None:
        self.findings.append(Finding("mutation", f"global {', '.join(node.names)}", node.lineno))
        self.generic_visit(node)

    def visit_Nonlocal(self, node: ast.Nonlocal) -> None:
        self.findings.append(Finding("mutation", f"nonlocal {', '.join(node.names)}", node.lineno))
        self.generic_visit(node)

    def visit_Call(self, node: ast.Call) -> None:
        name = _dotted(node.func)
        if name in _SEEDED_CONSTRUCTORS:
            # random.Random(seed) is deterministic given a declared seed.
            self._seeded_ok = True
        elif name in _PERTURBING_CALLS:
            # random.random() on a seeded instance is fine; on the module, not.
            if not (self._seeded_ok and name.startswith("random.")):
                self.findings.append(Finding(_PERTURBING_CALLS[name], name, node.lineno))
        self.generic_visit(node)

    def visit_Name(self, node: ast.Name) -> None:
        if isinstance(node.ctx, ast.Load) and node.id not in self._bound:
            self.reads_free_names.add(node.id)
        self.generic_visit(node)


def classify(fn) -> Verdict:
    src = textwrap.dedent(inspect.getsource(fn))
    tree = ast.parse(src)
    walker = _Walker()
    # Bind args first so free-name detection is meaningful.
    for n in ast.walk(tree):
        if isinstance(n, ast.arg):
            walker._bound.add(n.arg)
        if isinstance(n, (ast.FunctionDef, ast.AsyncFunctionDef)):
            walker._bound.add(n.name)
    walker.visit(tree)

    # Coverage gaps: free names that resolve to module globals are inputs the
    # code hash does NOT cover (P-25). Builtins are excluded as uninteresting.
    import builtins

    module = inspect.getmodule(fn)
    gaps = []
    for name in sorted(walker.reads_free_names):
        if hasattr(builtins, name):
            continue
        if module is not None and hasattr(module, name):
            target = getattr(module, name)
            if callable(target):
                gaps.append(f"{name}()")  # a called dependency, hashed separately
            else:
                gaps.append(name)  # a module constant — NOT covered by our hash

    kind = "perturbing" if walker.findings else "pure"
    return Verdict(fn.__name__, kind, walker.findings, gaps)
