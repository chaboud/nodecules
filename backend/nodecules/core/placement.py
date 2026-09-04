"""Placement — the plan as an artifact (PR-d2).

Where does compute run? The founding brief's complaint is that this gets
decided at deployment time by whatever process happens to own the code.
Here it is decided at *plan* time, as data, from four inputs:

  a graph of jobs      — each node names a description (what it must do,
                         PR-d1), the edges say who feeds whom;
  executors            — advertisements (keyhole's `hello` as a record):
                         locality, the claims each can serve, per-realization
                         cost, what is already warm;
  a policy             — the lock level (`open` / `no-model-egress` /
                         `full-airgap`) and what crossing between executors
                         costs;
  the evidence         — NodeSpecs and assay results the satisfies judgment
                         needs, so nothing is placed on a claim alone.

The output is a **plan**: node → (executor, realization), the regions that
induces, the cost split into compute and boundary crossings, and a reason
for every choice and every exclusion. It is content-addressed and
re-verifiable, and it answers "where did my data go" from its own record
(`data_flow`) — the trust layer's falsifiability requirement.

Three commitments, each measured in `spikes/placement-bench/`:

- **Locks are planning constraints, not egress checks.** An executor the
  lock does not admit is excluded before any cost is looked at, with the
  reason written down. An unsatisfiable policy fails *here*, loudly, with
  the job named — not at 3am.
- **Binding granularity is a region, not a node** (vault ADR-0015). The
  per-node method exists to be measured against: it picks each node's
  cheapest executor and pays the boundary crossings afterwards, which is
  E7's silent blow-up. The region method minimises compute plus crossings
  together (branch-and-bound over admissible executors; small graphs only,
  a partitioner comes later).
- **Plans and executor records are decoration.** They cite graphs,
  descriptions, and realizations by identity; nothing functional cites
  them back (`descriptions.assert_functional` covers the `plans/` and
  `executors/` namespaces).
"""

from __future__ import annotations

import hashlib
import json
from typing import Any, Dict, List, Literal, Mapping, Optional, Sequence, Set, Tuple

from pydantic import BaseModel, ConfigDict, Field

from nodecules.core.descriptions import AssayResult, Description, SatisfiesClaim, decide
from nodecules.core.types import NodeSpec

Locality = Literal["on-device", "lan", "cloud"]
LockLevel = Literal["open", "no-model-egress", "full-airgap"]
Method = Literal["region", "per-node"]

_ADMITS: Dict[str, Set[str]] = {
    "open": {"on-device", "lan", "cloud"},
    "no-model-egress": {"on-device", "lan"},
    "full-airgap": {"on-device"},
}


def lock_admits(lock: LockLevel, locality: Locality) -> bool:
    """keyhole's lock levels as a planning predicate: `no-model-egress`
    keeps compute off the cloud, `full-airgap` keeps it on the device."""
    return locality in _ADMITS[lock]


def _content_hash(payload: Any) -> str:
    return hashlib.sha256(
        json.dumps(payload, sort_keys=True, separators=(",", ":")).encode("utf-8")
    ).hexdigest()


# --- Inputs -------------------------------------------------------------------


class Executor(BaseModel):
    """An executor's advertisement. Decoration: it cites realizations and
    descriptions (through its claims) and is never cited by them."""

    model_config = ConfigDict(frozen=True)
    executor_id: str = Field(min_length=1)
    locality: Locality
    claims: Tuple[SatisfiesClaim, ...] = ()
    # Cost units to run a realization here — declared or measured (the
    # hardware runs replace declared numbers with measured ones).
    cost: Dict[str, float] = Field(default_factory=dict)
    warm: Tuple[str, ...] = ()  # realizations already resident
    cold_start: Dict[str, float] = Field(default_factory=dict)  # extra cost when not warm


class Job(BaseModel):
    """One node's job: the description it must satisfy, and the strips it
    touches (so the plan can say where data went)."""

    model_config = ConfigDict(frozen=True)
    node_id: str = Field(min_length=1)
    description: Description
    reads: Tuple[str, ...] = ()
    writes: Tuple[str, ...] = ()
    # Data locality: a job whose input exists only on one executor (the
    # media file on the device, a live mic) is pinned there. Pinning is a
    # hard constraint, recorded as an exclusion reason on every other
    # executor — the plan says why the crossing was unavoidable.
    pinned_to: Optional[str] = None


class PlacementGraph(BaseModel):
    model_config = ConfigDict(frozen=True)
    graph_id: str = Field(min_length=1)
    jobs: Tuple[Job, ...] = Field(min_length=1)
    edges: Tuple[Tuple[str, str], ...] = ()  # (source node_id, target node_id)

    def content_hash(self) -> str:
        return _content_hash({
            "graph_id": self.graph_id,
            "jobs": [
                {"node_id": j.node_id, "description": j.description.content_hash(),
                 "reads": list(j.reads), "writes": list(j.writes), "pinned_to": j.pinned_to}
                for j in self.jobs
            ],
            "edges": [list(e) for e in self.edges],
        })


class Policy(BaseModel):
    """What the plan must respect and what it must pay for."""

    model_config = ConfigDict(frozen=True)
    lock_level: LockLevel = "open"
    # Crossing cost between two *different* executors, keyed by the sorted
    # locality pair, e.g. "lan|on-device". Missing pairs use the default.
    boundary_cost: Dict[str, float] = Field(default_factory=dict)
    default_boundary: float = 1.0

    def crossing_cost(self, a: Locality, b: Locality) -> float:
        key = "|".join(sorted((a, b)))
        return self.boundary_cost.get(key, self.default_boundary)

    def content_hash(self) -> str:
        return _content_hash(self.model_dump(mode="json"))


# --- Outputs ------------------------------------------------------------------


class Candidate(BaseModel):
    model_config = ConfigDict(frozen=True)
    executor_id: str
    realization: str
    compute_cost: float
    hallmark_hash: str


class Assignment(BaseModel):
    model_config = ConfigDict(frozen=True)
    node_id: str
    executor_id: str
    realization: str
    compute_cost: float
    reason: str


class Plan(BaseModel):
    """The artifact. Decoration about a graph; content-addressed."""

    model_config = ConfigDict(frozen=True)
    graph_hash: str
    policy_hash: str
    method: Method
    assignments: Tuple[Assignment, ...]
    regions: Tuple[Tuple[str, Tuple[str, ...]], ...]  # (executor_id, node_ids), connected
    compute_cost: float
    boundary_cost: float
    crossings: int
    excluded: Dict[str, Dict[str, str]]  # node_id -> executor_id -> why not

    @property
    def total_cost(self) -> float:
        return self.compute_cost + self.boundary_cost

    def executor_of(self, node_id: str) -> str:
        for a in self.assignments:
            if a.node_id == node_id:
                return a.executor_id
        raise KeyError(node_id)

    def content_hash(self) -> str:
        return _content_hash(self.model_dump(mode="json"))


class Placement(BaseModel):
    """`plan is None` is an answer: the policy is unsatisfiable for the
    named jobs, and `unsatisfiable` says why per executor."""

    model_config = ConfigDict(frozen=True)
    plan: Optional[Plan] = None
    unsatisfiable: Dict[str, Dict[str, str]] = Field(default_factory=dict)


# --- Candidates: locks first, then the satisfies judgment ------------------


def candidates(
    job: Job,
    executors: Sequence[Executor],
    specs: Mapping[str, NodeSpec],
    assays: Mapping[str, AssayResult],
    policy: Policy,
) -> Tuple[Dict[str, Candidate], Dict[str, str]]:
    """Which executors may run this job, and why the others may not."""
    admitted: Dict[str, Candidate] = {}
    excluded: Dict[str, str] = {}
    for ex in executors:
        if job.pinned_to is not None and ex.executor_id != job.pinned_to:
            excluded[ex.executor_id] = f"job is pinned to {job.pinned_to!r} (data locality)"
            continue
        if not lock_admits(policy.lock_level, ex.locality):
            excluded[ex.executor_id] = f"lock {policy.lock_level!r} does not admit {ex.locality!r}"
            continue
        binding = decide(job.description, ex.claims, specs, assays)
        if binding.chosen is None:
            if binding.rejected:
                why = "; ".join(f"{r}: {w}" for r, w in binding.rejected.items())
                excluded[ex.executor_id] = f"no passing claim ({why})"
            else:
                excluded[ex.executor_id] = f"no claim for {job.description.name!r}"
            continue
        realization = binding.chosen.realization
        if realization not in ex.cost:
            excluded[ex.executor_id] = f"no cost declared for {realization!r}"
            continue
        cost = ex.cost[realization]
        if realization not in ex.warm:
            cost += ex.cold_start.get(realization, 0.0)
        admitted[ex.executor_id] = Candidate(
            executor_id=ex.executor_id, realization=realization, compute_cost=cost,
            hallmark_hash=binding.chosen.hallmark.content_hash(),
        )
    return admitted, excluded


# --- The plan -----------------------------------------------------------------


def _topological(graph: PlacementGraph) -> List[str]:
    ids = [j.node_id for j in graph.jobs]
    known = set(ids)
    for s, t in graph.edges:
        if s not in known or t not in known:
            raise ValueError(f"edge {s!r}->{t!r} names an unknown node")
    indeg = {n: 0 for n in ids}
    out: Dict[str, List[str]] = {n: [] for n in ids}
    for s, t in graph.edges:
        indeg[t] += 1
        out[s].append(t)
    ready = [n for n in ids if indeg[n] == 0]
    order: List[str] = []
    while ready:
        n = ready.pop(0)
        order.append(n)
        for m in out[n]:
            indeg[m] -= 1
            if indeg[m] == 0:
                ready.append(m)
    if len(order) != len(ids):
        raise ValueError("graph has a cycle; placement needs a DAG")
    return order


def _neighbours(graph: PlacementGraph) -> Dict[str, List[str]]:
    adj: Dict[str, List[str]] = {j.node_id: [] for j in graph.jobs}
    for s, t in graph.edges:
        adj[s].append(t)
        adj[t].append(s)
    return adj


def _regions(graph: PlacementGraph, assign: Mapping[str, str]) -> Tuple[Tuple[str, Tuple[str, ...]], ...]:
    """Connected components of the graph induced on each executor."""
    adj = _neighbours(graph)
    seen: Set[str] = set()
    regions: List[Tuple[str, Tuple[str, ...]]] = []
    for j in graph.jobs:
        n = j.node_id
        if n in seen:
            continue
        ex = assign[n]
        stack, comp = [n], []
        seen.add(n)
        while stack:
            cur = stack.pop()
            comp.append(cur)
            for m in adj[cur]:
                if m not in seen and assign[m] == ex:
                    seen.add(m)
                    stack.append(m)
        regions.append((ex, tuple(sorted(comp))))
    return tuple(regions)


def _boundary(graph: PlacementGraph, assign: Mapping[str, str],
              loc: Mapping[str, str], policy: Policy) -> Tuple[float, int]:
    cost, crossings = 0.0, 0
    for s, t in graph.edges:
        if assign[s] != assign[t]:
            crossings += 1
            cost += policy.crossing_cost(loc[assign[s]], loc[assign[t]])  # type: ignore[arg-type]
    return cost, crossings


def plan(
    graph: PlacementGraph,
    executors: Sequence[Executor],
    specs: Mapping[str, NodeSpec],
    assays: Mapping[str, AssayResult],
    policy: Policy,
    *,
    method: Method = "region",
    max_nodes: int = 16,
) -> Placement:
    """Produce the plan, or say why none exists.

    `per-node`: each node takes its cheapest admissible executor and the
    crossings are what they are — the baseline to be measured against.
    `region`: branch-and-bound over admissible executors, minimising
    compute + crossings jointly. Exhaustive in the worst case, so capped
    at `max_nodes`; beyond that bring a partitioner, do not raise the cap.
    """
    order = _topological(graph)
    if len(order) > max_nodes:
        raise ValueError(f"region placement is exhaustive; {len(order)} nodes exceeds max_nodes={max_nodes}")
    loc = {ex.executor_id: ex.locality for ex in executors}
    jobs = {j.node_id: j for j in graph.jobs}

    cands: Dict[str, Dict[str, Candidate]] = {}
    excluded: Dict[str, Dict[str, str]] = {}
    for n in order:
        c, x = candidates(jobs[n], executors, specs, assays, policy)
        cands[n], excluded[n] = c, x
    unsat = {n: excluded[n] for n in order if not cands[n]}
    if unsat:
        return Placement(plan=None, unsatisfiable=unsat)

    adj = _neighbours(graph)
    assign: Dict[str, str] = {}

    if method == "per-node":
        for n in order:
            best = min(cands[n].values(), key=lambda c: (c.compute_cost, c.executor_id))
            assign[n] = best.executor_id
    else:
        best_cost = [float("inf")]
        best_assign: Dict[str, str] = {}

        def rec(i: int, cost: float) -> None:
            if cost >= best_cost[0]:
                return
            if i == len(order):
                best_cost[0] = cost
                best_assign.clear()
                best_assign.update(assign)
                return
            n = order[i]
            for c in sorted(cands[n].values(), key=lambda c: (c.compute_cost, c.executor_id)):
                add = c.compute_cost
                for m in adj[n]:
                    if m in assign and assign[m] != c.executor_id:
                        add += policy.crossing_cost(loc[c.executor_id], loc[assign[m]])  # type: ignore[arg-type]
                assign[n] = c.executor_id
                rec(i + 1, cost + add)
                del assign[n]

        rec(0, 0.0)
        assign = dict(best_assign)

    assignments = []
    compute = 0.0
    for n in order:
        c = cands[n][assign[n]]
        compute += c.compute_cost
        others = sorted(cands[n]) 
        reason = (
            f"{c.executor_id} runs {c.realization} at {c.compute_cost:.3g}; "
            f"admissible: {', '.join(others)}; excluded: {', '.join(sorted(excluded[n])) or 'none'}"
        )
        assignments.append(Assignment(node_id=n, executor_id=c.executor_id, realization=c.realization,
                                      compute_cost=c.compute_cost, reason=reason))
    bcost, crossings = _boundary(graph, assign, loc, policy)
    return Placement(plan=Plan(
        graph_hash=graph.content_hash(), policy_hash=policy.content_hash(), method=method,
        assignments=tuple(assignments), regions=_regions(graph, assign),
        compute_cost=compute, boundary_cost=bcost, crossings=crossings, excluded=excluded,
    ))


# --- Reading the plan back ------------------------------------------------------


def data_flow(p: Plan, graph: PlacementGraph) -> Dict[str, Tuple[str, ...]]:
    """Where did my data go: every strip a job reads or writes, mapped to
    the executors that touched it. Complete by construction — it is read
    off the plan record, not off the runtime."""
    touched: Dict[str, Set[str]] = {}
    for j in graph.jobs:
        ex = p.executor_of(j.node_id)
        for strip in (*j.reads, *j.writes):
            touched.setdefault(strip, set()).add(ex)
    return {strip: tuple(sorted(exs)) for strip, exs in sorted(touched.items())}


def verify_plan(p: Plan, graph: PlacementGraph, executors: Sequence[Executor],
                policy: Policy) -> Tuple[bool, str]:
    """Trust nothing recomputable: the graph and policy it names, and the
    costs it claims, are re-derived from content."""
    if p.graph_hash != graph.content_hash():
        return False, "plan names a different graph"
    if p.policy_hash != policy.content_hash():
        return False, "plan names a different policy"
    loc = {ex.executor_id: ex.locality for ex in executors}
    by_id = {ex.executor_id: ex for ex in executors}
    assign = {a.node_id: a.executor_id for a in p.assignments}
    if set(assign) != {j.node_id for j in graph.jobs}:
        return False, "plan does not assign exactly the graph's nodes"
    compute = 0.0
    for a in p.assignments:
        ex = by_id.get(a.executor_id)
        if ex is None or a.realization not in ex.cost:
            return False, f"{a.node_id}: executor or cost unknown"
        if not lock_admits(policy.lock_level, ex.locality):
            return False, f"{a.node_id}: {a.executor_id} is not admitted by lock {policy.lock_level!r}"
        expected = ex.cost[a.realization] + (0.0 if a.realization in ex.warm else ex.cold_start.get(a.realization, 0.0))
        if abs(expected - a.compute_cost) > 1e-9:
            return False, f"{a.node_id}: compute cost {a.compute_cost} does not match executor record {expected}"
        compute += expected
    bcost, crossings = _boundary(graph, assign, loc, policy)
    if abs(compute - p.compute_cost) > 1e-9 or abs(bcost - p.boundary_cost) > 1e-9 or crossings != p.crossings:
        return False, "plan's cost summary does not match its assignments"
    return True, "graph, policy, assignments, and costs re-derive consistently"


__all__ = [
    "Assignment", "Candidate", "Executor", "Job", "Locality", "LockLevel", "Method",
    "Placement", "PlacementGraph", "Plan", "Policy",
    "candidates", "data_flow", "lock_admits", "plan", "verify_plan",
]
