"""Placement — the plan as an artifact (PR-d2).

Where does compute run? The founding brief's complaint is that this gets
decided at deployment time by whatever process happens to own the code.
Here it is decided at *plan time*, as data, from four inputs:

  a graph of jobs      — each node names a description (what it must do,
                         PR-d1), the edges say who feeds whom and how many
                         bytes cross;
  executors            — advertisements (keyhole's `hello` as a record):
                         host and device, locality, the claims each serves,
                         per-realization cost *vectors*, memory capacity,
                         what is already warm;
  a policy             — the lock level; how each boundary kind (bus, lan,
                         wan) is priced, fixed and per byte; the *weights*
                         that fold cost dimensions into the objective; and
                         the *compound heuristics* it applies;
  the evidence         — NodeSpecs and assay results the satisfies judgment
                         needs, so nothing is placed on a claim alone.

The output is a **plan**: node → (executor, realization), the regions that
induces, the cost broken into compute / boundary / heuristics as vectors
and as the folded objective, every heuristic that fired and where, and a
reason for every choice and every exclusion. It is content-addressed and
re-verifiable, and it answers "where did my data go" from its own record.

The cost model (founder, 2026-08-29): **nodes and edges both cost, in
several dimensions, and edge costs compound.** A node has compute,
memory, and bus costs that land on latency, energy, and money. An edge
that crosses a machine or component boundary pays network, service, or
bus costs — and those compound across a graph: a CPU → GPU → CPU
ping-pong can be the fastest plan node-by-node and the worst in practice,
because bouncing breaks pipelining and cache-friendly mechanics. So:

- costs are vectors (`CostVector`) and the policy's `weights` fold them —
  a phone weighs energy and thermals, a datacenter weighs money;
- data locality is a cost (bytes × the boundary's per-byte price) with the
  pin as its infinite case, and memory capacity is a constraint;
- boundary *kind* is derived from the executors' host/device/locality —
  `bus` within a host, `lan` between hosts, `wan` when the cloud is
  involved — and priced per kind;
- compound heuristics are **declared data** in the policy (`heuristics`),
  applied uniformly by the optimiser and reported by name on the plan:
  system-design judgment encoded once, then algorithmic.

What the substrate commits to, and what it does not (founder, 2026-08-29):
the folded objective is **one policy, the dumbest**, deliberately. Multi-
dimensional cost cannot be reduced to a vector and a fixed weighting —
linear scalarization misses the non-convex parts of the Pareto front, and
with compounding edge effects optimal aggregation is NP-hard in general. A
system may instead learn its own cost geometry (its own PCA over observed
runs, a Lipschitz-regularized surrogate, a human's judgment). The
substrate's job is to make that *possible* by providing **identity** —
every plan, assignment, and crossing content-addressed, so an observation
attaches to exactly the thing that ran — and **observability** — measured
costs emitted as records against those identities in every dimension,
with what fired. Hence `plan_front` (hand up the non-dominated set, let the
policy choose) and `Observation` / `reconcile` (measured against declared,
keyed by identity). The aggregator is an upper layer.

Three commitments, each measured in `spikes/placement-bench/`:

- **Locks are planning constraints, not egress checks.** Excluded before
  cost is looked at, reason recorded; an unsatisfiable policy fails here.
- **Binding granularity is a region, not a node** (vault ADR-0015). The
  per-node method exists to be measured against; the region method
  minimises the folded objective jointly by branch-and-bound.
- **Plans and executor records are decoration.** They cite graphs,
  descriptions, and realizations by identity; nothing functional cites
  them back.
"""

from __future__ import annotations

import hashlib
import json
from typing import Any, Dict, List, Literal, Mapping, Optional, Sequence, Set, Tuple, Union

from pydantic import BaseModel, ConfigDict, Field

from nodecules.core.descriptions import AssayResult, Description, SatisfiesClaim, decide
from nodecules.core.types import NodeSpec

Locality = Literal["on-device", "lan", "cloud"]
LockLevel = Literal["open", "no-model-egress", "full-airgap"]
Method = Literal["region", "per-node", "front"]
BoundaryKind = Literal["bus", "lan", "wan"]

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


# --- Costs ----------------------------------------------------------------------


class CostVector(BaseModel):
    """Additive cost dimensions. Units are whatever the executor declared
    (seconds, joules, currency); the policy's weights make them
    commensurable. Memory is *not* here — peak footprint is a capacity
    constraint, not a sum."""

    model_config = ConfigDict(frozen=True)
    latency: float = 0.0
    energy: float = 0.0
    money: float = 0.0

    def __add__(self, other: "CostVector") -> "CostVector":
        return CostVector(latency=self.latency + other.latency,
                          energy=self.energy + other.energy,
                          money=self.money + other.money)

    def scale(self, k: float) -> "CostVector":
        return CostVector(latency=self.latency * k, energy=self.energy * k, money=self.money * k)

    def fold(self, weights: Mapping[str, float]) -> float:
        return (self.latency * weights.get("latency", 0.0)
                + self.energy * weights.get("energy", 0.0)
                + self.money * weights.get("money", 0.0))


ZERO = CostVector()


def _as_vector(v: Union[float, int, CostVector]) -> CostVector:
    return v if isinstance(v, CostVector) else CostVector(latency=float(v))


# --- Inputs -------------------------------------------------------------------


class Executor(BaseModel):
    """An executor's advertisement. Decoration: it cites realizations and
    descriptions (through its claims) and is never cited by them."""

    model_config = ConfigDict(frozen=True)
    executor_id: str = Field(min_length=1)
    locality: Locality
    # Where it physically is: two executors on one host but different
    # devices (a CPU and a GPU) are separated by a *bus*, not a network.
    host: Optional[str] = None  # defaults to executor_id
    device: str = "cpu"
    # Accelerators with pipeline / cache state pay to fill and flush it on
    # every crossing in or out — the `pipeline_fill_flush` heuristic.
    pipelined: bool = False
    claims: Tuple[SatisfiesClaim, ...] = ()
    # Cost per realization: a bare number is latency; a vector is the full
    # declaration. Declared or measured (the hardware runs replace them).
    cost: Dict[str, Union[float, CostVector]] = Field(default_factory=dict)
    warm: Tuple[str, ...] = ()
    cold_start: Dict[str, Union[float, CostVector]] = Field(default_factory=dict)
    memory_bytes: Optional[float] = None  # capacity; None = unconstrained

    @property
    def host_id(self) -> str:
        return self.host or self.executor_id

    def cost_of(self, realization: str) -> Optional[CostVector]:
        if realization not in self.cost:
            return None
        v = _as_vector(self.cost[realization])
        if realization not in self.warm and realization in self.cold_start:
            v = v + _as_vector(self.cold_start[realization])
        return v


class Job(BaseModel):
    """One node's job: the description it must satisfy, the strips it
    touches (so the plan can say where data went), and what it needs."""

    model_config = ConfigDict(frozen=True)
    node_id: str = Field(min_length=1)
    description: Description
    reads: Tuple[str, ...] = ()
    writes: Tuple[str, ...] = ()
    # Data locality as a constraint — the infinite-cost case. A job whose
    # input exists only on one executor (the media file, a live mic) runs
    # there, and every other executor's exclusion says so.
    pinned_to: Optional[str] = None
    memory_bytes: Optional[float] = None  # peak footprint; must fit the executor


class Edge(BaseModel):
    """Who feeds whom, and how much crosses if they are apart."""

    model_config = ConfigDict(frozen=True)
    source: str
    target: str
    bytes: float = 0.0


class PlacementGraph(BaseModel):
    model_config = ConfigDict(frozen=True)
    graph_id: str = Field(min_length=1)
    jobs: Tuple[Job, ...] = Field(min_length=1)
    edges: Tuple[Union[Tuple[str, str], Edge], ...] = ()

    def edge_list(self) -> List[Edge]:
        return [e if isinstance(e, Edge) else Edge(source=e[0], target=e[1]) for e in self.edges]

    def content_hash(self) -> str:
        return _content_hash({
            "graph_id": self.graph_id,
            "jobs": [
                {"node_id": j.node_id, "description": j.description.content_hash(),
                 "reads": list(j.reads), "writes": list(j.writes),
                 "pinned_to": j.pinned_to, "memory_bytes": j.memory_bytes}
                for j in self.jobs
            ],
            "edges": [[e.source, e.target, e.bytes] for e in self.edge_list()],
        })


class BoundaryPrice(BaseModel):
    """What one kind of crossing costs: a fixed part and a per-byte part."""

    model_config = ConfigDict(frozen=True)
    fixed: CostVector = ZERO
    per_byte: CostVector = ZERO


# The compound heuristics the policy may declare, by name. Each is a
# penalty (never a bonus) so branch-and-bound's lower bound stays valid —
# "keep the pipeline together" is expressed as "pay to break it".
HEURISTICS: Dict[str, str] = {
    "pingpong": (
        "Data leaves an executor and comes back: a node placed on X fed by a node "
        "on Y whose ancestry includes X, at any distance. The return crossing is "
        "multiplied by the parameter, because bouncing breaks pipelining and cache "
        "warmth beyond what the crossings cost alone."
    ),
    "pipeline_fill_flush": (
        "Every crossing into or out of a pipelined executor pays the parameter in "
        "latency: accelerators fill and flush pipeline / cache state at each entry "
        "and exit, so fragmenting a run across boundaries costs per fragment."
    ),
}


class Policy(BaseModel):
    """What the plan must respect, what it must pay for, and how it weighs
    the dimensions — the judgment, as data."""

    model_config = ConfigDict(frozen=True)
    lock_level: LockLevel = "open"
    # Legacy scalar pricing: latency per crossing, by sorted locality pair
    # ("lan|on-device"). Used when `boundary` has no entry for the kind.
    boundary_cost: Dict[str, float] = Field(default_factory=dict)
    default_boundary: float = 1.0
    # Pricing by boundary kind, fixed + per byte, in all dimensions.
    boundary: Dict[str, BoundaryPrice] = Field(default_factory=dict)
    # The biases: how dimensions fold into one objective. A phone weighs
    # energy; a datacenter weighs money. Unlisted dimensions weigh zero.
    weights: Dict[str, float] = Field(default_factory=lambda: {"latency": 1.0})
    # Compound heuristics by name -> parameter. See HEURISTICS.
    heuristics: Dict[str, float] = Field(default_factory=dict)

    def content_hash(self) -> str:
        return _content_hash(self.model_dump(mode="json"))

    def crossing(self, a: Executor, b: Executor, edge_bytes: float = 0.0) -> Tuple[BoundaryKind, CostVector]:
        """The kind and cost of moving `edge_bytes` from a to b."""
        kind = boundary_kind(a, b)
        price = self.boundary.get(kind)
        if price is not None:
            return kind, price.fixed + price.per_byte.scale(edge_bytes)
        key = "|".join(sorted((a.locality, b.locality)))
        return kind, CostVector(latency=self.boundary_cost.get(key, self.default_boundary))


def boundary_kind(a: Executor, b: Executor) -> BoundaryKind:
    if a.locality == "cloud" or b.locality == "cloud":
        return "wan"
    if a.host_id == b.host_id:
        return "bus"
    return "lan"


# --- Outputs ------------------------------------------------------------------


class Candidate(BaseModel):
    model_config = ConfigDict(frozen=True)
    executor_id: str
    realization: str
    compute: CostVector
    hallmark_hash: str


class Assignment(BaseModel):
    model_config = ConfigDict(frozen=True)
    node_id: str
    executor_id: str
    realization: str
    compute: CostVector
    reason: str

    @property
    def compute_cost(self) -> float:
        return self.compute.latency


class HeuristicHit(BaseModel):
    """A declared heuristic that fired: which one, where, and what it cost."""

    model_config = ConfigDict(frozen=True)
    name: str
    nodes: Tuple[str, ...]
    penalty: CostVector


class Plan(BaseModel):
    """The artifact. Decoration about a graph; content-addressed."""

    model_config = ConfigDict(frozen=True)
    graph_hash: str
    policy_hash: str
    method: Method
    assignments: Tuple[Assignment, ...]
    regions: Tuple[Tuple[str, Tuple[str, ...]], ...]  # (executor_id, node_ids), connected
    crossings: int
    compute: CostVector
    boundary: CostVector
    heuristic: CostVector
    heuristic_hits: Tuple[HeuristicHit, ...] = ()
    objective: float  # the folded total: what the optimiser minimised
    compute_cost: float  # folded compute, for reading
    boundary_cost: float  # folded boundary
    heuristic_cost: float = 0.0  # folded heuristics
    excluded: Dict[str, Dict[str, str]]  # node_id -> executor_id -> why not

    @property
    def total_cost(self) -> float:
        return self.objective

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


# --- Candidates: constraints first, then the satisfies judgment --------------


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
        if job.memory_bytes is not None and ex.memory_bytes is not None and job.memory_bytes > ex.memory_bytes:
            excluded[ex.executor_id] = (
                f"needs {job.memory_bytes:.3g} bytes of memory; executor has {ex.memory_bytes:.3g}"
            )
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
        vec = ex.cost_of(realization)
        if vec is None:
            excluded[ex.executor_id] = f"no cost declared for {realization!r}"
            continue
        admitted[ex.executor_id] = Candidate(
            executor_id=ex.executor_id, realization=realization, compute=vec,
            hallmark_hash=binding.chosen.hallmark.content_hash(),
        )
    return admitted, excluded


# --- The plan -----------------------------------------------------------------


def _topological(graph: PlacementGraph, edges: Sequence[Edge]) -> List[str]:
    ids = [j.node_id for j in graph.jobs]
    known = set(ids)
    for e in edges:
        if e.source not in known or e.target not in known:
            raise ValueError(f"edge {e.source!r}->{e.target!r} names an unknown node")
    indeg = {n: 0 for n in ids}
    out: Dict[str, List[str]] = {n: [] for n in ids}
    for e in edges:
        indeg[e.target] += 1
        out[e.source].append(e.target)
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


def _regions(graph: PlacementGraph, edges: Sequence[Edge],
             assign: Mapping[str, str]) -> Tuple[Tuple[str, Tuple[str, ...]], ...]:
    """Connected components of the graph induced on each executor."""
    adj: Dict[str, List[str]] = {j.node_id: [] for j in graph.jobs}
    for e in edges:
        adj[e.source].append(e.target)
        adj[e.target].append(e.source)
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


class _Evaluator:
    """Incremental cost of a (partial) assignment, node by node in
    topological order, so branch-and-bound can prune. Every contribution
    is a penalty, so the partial cost is a lower bound on the total."""

    def __init__(self, graph: PlacementGraph, edges: Sequence[Edge],
                 executors: Sequence[Executor], policy: Policy) -> None:
        self.policy = policy
        self.ex = {e.executor_id: e for e in executors}
        self.pipelined = {e.executor_id: int(e.pipelined) for e in executors}
        self._xcache: Dict[Tuple[str, str, float], Tuple[float, float, float]] = {}
        self.in_edges: Dict[str, List[Edge]] = {j.node_id: [] for j in graph.jobs}
        self.out_edges: Dict[str, List[Edge]] = {j.node_id: [] for j in graph.jobs}
        for e in edges:
            self.in_edges[e.target].append(e)
            self.out_edges[e.source].append(e)
        # Transitive ancestors, for round-trip detection at any distance.
        self.ancestors: Dict[str, Set[str]] = {}
        for n in _topological(graph, edges):
            anc: Set[str] = set()
            for e in self.in_edges[n]:
                anc.add(e.source)
                anc |= self.ancestors[e.source]
            self.ancestors[n] = anc

    def crossing(self, a: str, b: str, nbytes: float) -> CostVector:
        return self.policy.crossing(self.ex[a], self.ex[b], nbytes)[1]

    def _xr(self, a: str, b: str, nbytes: float) -> Tuple[float, float, float]:
        key = (a, b, nbytes)
        hit = self._xcache.get(key)
        if hit is None:
            v = self.crossing(a, b, nbytes)
            hit = self._xcache[key] = (v.latency, v.energy, v.money)
        return hit

    def on_assign_raw(self, node: str, exid: str, assign: Mapping[str, str]) -> Tuple[float, float, float]:
        """Same accounting as `on_assign`, as a bare (latency, energy, money)
        tuple with no records built — the search loops call this hundreds
        of thousands of times; the record path is walked once."""
        lat = en = mo = 0.0
        h = self.policy.heuristics
        fill = h.get("pipeline_fill_flush")
        pingpong = h.get("pingpong")
        for e in self.in_edges[node]:
            a = assign.get(e.source)
            if a is None or a == exid:
                continue
            x = self._xr(a, exid, e.bytes)
            lat += x[0]; en += x[1]; mo += x[2]
            if fill is not None:
                lat += fill * (self.pipelined[a] + self.pipelined[exid])
            if pingpong is not None and any(assign.get(q) == exid for q in self.ancestors[e.source]):
                k = pingpong - 1.0
                lat += x[0] * k; en += x[1] * k; mo += x[2] * k
        for e in self.out_edges[node]:
            b = assign.get(e.target)
            if b is None or b == exid:
                continue
            x = self._xr(exid, b, e.bytes)
            lat += x[0]; en += x[1]; mo += x[2]
            if fill is not None:
                lat += fill * (self.pipelined[exid] + self.pipelined[b])
        return lat, en, mo

    def on_assign(self, node: str, exid: str, assign: Mapping[str, str]
                  ) -> Tuple[CostVector, CostVector, int, List[HeuristicHit]]:
        """Boundary and heuristic cost that becomes determined when `node`
        is placed on `exid`, given the nodes already placed. Edges are
        charged when their second endpoint is placed; each heuristic is
        charged at the node that completes its pattern."""
        boundary = ZERO
        heur = ZERO
        crossings = 0
        hits: List[HeuristicHit] = []
        h = self.policy.heuristics
        touched: List[Edge] = [e for e in self.in_edges[node] if e.source in assign] + \
                              [e for e in self.out_edges[node] if e.target in assign]
        for e in touched:
            a = assign.get(e.source, exid) if e.source != node else exid
            b = assign.get(e.target, exid) if e.target != node else exid
            if a == b:
                continue
            crossings += 1
            boundary = boundary + self.crossing(a, b, e.bytes)
            if "pipeline_fill_flush" in h:
                for side in (a, b):
                    if self.ex[side].pipelined:
                        pen = CostVector(latency=h["pipeline_fill_flush"])
                        heur = heur + pen
                        hits.append(HeuristicHit(name="pipeline_fill_flush",
                                                 nodes=(e.source, e.target), penalty=pen))
        if "pingpong" in h:
            factor = h["pingpong"]
            # A round trip completes here: node on X, fed by p on Y, and
            # somewhere upstream of p the data was already on X.
            for e_pn in self.in_edges[node]:
                p = e_pn.source
                if p not in assign or assign[p] == exid:
                    continue
                origin = sorted(q for q in self.ancestors[p] if assign.get(q) == exid)
                if origin:
                    pen = self.crossing(assign[p], exid, e_pn.bytes).scale(factor - 1.0)
                    heur = heur + pen
                    hits.append(HeuristicHit(name="pingpong", nodes=(origin[0], p, node), penalty=pen))
        return boundary, heur, crossings, hits


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

    `per-node`: each node takes the executor with the cheapest folded
    compute and the crossings and heuristics are what they are — the
    baseline to be measured against. `region`: branch-and-bound over
    admissible executors, minimising the folded objective (compute +
    boundary + heuristics) jointly. Exhaustive in the worst case, so
    capped at `max_nodes`; beyond that bring a partitioner.
    """
    for name in policy.heuristics:
        if name not in HEURISTICS:
            raise ValueError(f"unknown heuristic {name!r}; known: {sorted(HEURISTICS)}")
    edges = graph.edge_list()
    order = _topological(graph, edges)
    if len(order) > max_nodes:
        raise ValueError(f"region placement is exhaustive; {len(order)} nodes exceeds max_nodes={max_nodes}")
    jobs = {j.node_id: j for j in graph.jobs}
    w = policy.weights

    cands: Dict[str, Dict[str, Candidate]] = {}
    excluded: Dict[str, Dict[str, str]] = {}
    for n in order:
        c, x = candidates(jobs[n], executors, specs, assays, policy)
        cands[n], excluded[n] = c, x
    unsat = {n: excluded[n] for n in order if not cands[n]}
    if unsat:
        return Placement(plan=None, unsatisfiable=unsat)

    ev = _Evaluator(graph, edges, executors, policy)
    assign: Dict[str, str] = {}

    def ranked(n: str) -> List[Candidate]:
        return sorted(cands[n].values(), key=lambda c: (c.compute.fold(w), c.executor_id))

    if method == "per-node":
        for n in order:
            assign[n] = ranked(n)[0].executor_id
    else:
        best_cost = [float("inf")]
        best_assign: Dict[str, str] = {}
        wl, we, wm = w.get("latency", 0.0), w.get("energy", 0.0), w.get("money", 0.0)

        def rec(i: int, cost: float) -> None:
            if cost >= best_cost[0]:
                return
            if i == len(order):
                best_cost[0] = cost
                best_assign.clear()
                best_assign.update(assign)
                return
            n = order[i]
            for c in ranked(n):
                lat, en, mo = ev.on_assign_raw(n, c.executor_id, assign)
                add = c.compute.fold(w) + lat * wl + en * we + mo * wm
                assign[n] = c.executor_id
                rec(i + 1, cost + add)
                del assign[n]

        rec(0, 0.0)
        assign = dict(best_assign)

    return Placement(plan=_build_plan(graph, edges, order, cands, excluded, ev, assign, policy, method))


def _build_plan(graph: PlacementGraph, edges: Sequence[Edge], order: Sequence[str],
                cands: Mapping[str, Mapping[str, Candidate]], excluded: Mapping[str, Mapping[str, str]],
                ev: "_Evaluator", assign: Mapping[str, str], policy: Policy, method: Method) -> Plan:
    """Walk a complete assignment in topological order and produce the record."""
    w = policy.weights
    assignments: List[Assignment] = []
    compute = ZERO
    boundary = ZERO
    heur = ZERO
    crossings = 0
    hits: List[HeuristicHit] = []
    placed: Dict[str, str] = {}
    for n in order:
        c = cands[n][assign[n]]
        b, hv, x, hh = ev.on_assign(n, c.executor_id, placed)
        placed[n] = c.executor_id
        compute = compute + c.compute
        boundary = boundary + b
        heur = heur + hv
        crossings += x
        hits.extend(hh)
        reason = (
            f"{c.executor_id} runs {c.realization} at {c.compute.fold(w):.3g}; "
            f"admissible: {', '.join(sorted(cands[n]))}; excluded: {', '.join(sorted(excluded[n])) or 'none'}"
        )
        assignments.append(Assignment(node_id=n, executor_id=c.executor_id, realization=c.realization,
                                      compute=c.compute, reason=reason))
    objective = compute.fold(w) + boundary.fold(w) + heur.fold(w)
    return Plan(
        graph_hash=graph.content_hash(), policy_hash=policy.content_hash(), method=method,
        assignments=tuple(assignments), regions=_regions(graph, edges, assign), crossings=crossings,
        compute=compute, boundary=boundary, heuristic=heur, heuristic_hits=tuple(hits),
        objective=objective, compute_cost=compute.fold(w), boundary_cost=boundary.fold(w),
        heuristic_cost=heur.fold(w), excluded=dict(excluded),
    )


def _dominates(a: CostVector, b: CostVector) -> bool:
    """a is at least as good as b in every dimension and better in one."""
    le = a.latency <= b.latency and a.energy <= b.energy and a.money <= b.money
    lt = a.latency < b.latency or a.energy < b.energy or a.money < b.money
    return le and lt


def plan_front(
    graph: PlacementGraph,
    executors: Sequence[Executor],
    specs: Mapping[str, NodeSpec],
    assays: Mapping[str, AssayResult],
    policy: Policy,
    *,
    max_nodes: int = 12,
) -> Tuple[Placement, Tuple[Plan, ...]]:
    """The Pareto front: every admissible plan that no other plan beats in
    all dimensions at once. The policy's weights are *not* applied to
    choose — that is the caller's (or a learned aggregator's) job; the
    substrate hands up the set with each plan's full vector and hash.
    Exhaustive over admissible assignments, so capped lower than `plan`.
    Heuristics apply (they are part of what a plan costs); the front is
    over compute + boundary + heuristic totals."""
    for name in policy.heuristics:
        if name not in HEURISTICS:
            raise ValueError(f"unknown heuristic {name!r}; known: {sorted(HEURISTICS)}")
    edges = graph.edge_list()
    order = _topological(graph, edges)
    if len(order) > max_nodes:
        raise ValueError(f"the front is exhaustive; {len(order)} nodes exceeds max_nodes={max_nodes}")
    jobs = {j.node_id: j for j in graph.jobs}
    cands: Dict[str, Dict[str, Candidate]] = {}
    excluded: Dict[str, Dict[str, str]] = {}
    for n in order:
        c, x = candidates(jobs[n], executors, specs, assays, policy)
        cands[n], excluded[n] = c, x
    unsat = {n: excluded[n] for n in order if not cands[n]}
    if unsat:
        return Placement(plan=None, unsatisfiable=unsat), ()

    ev = _Evaluator(graph, edges, executors, policy)
    found: List[Tuple[Tuple[float, float, float], Tuple[str, ...]]] = []
    assign: Dict[str, str] = {}
    comp = {n: {ex: (c.compute.latency, c.compute.energy, c.compute.money) for ex, c in cands[n].items()}
            for n in order}
    choice: List[str] = []

    def rec(i: int, lat: float, en: float, mo: float) -> None:
        if i == len(order):
            found.append(((lat, en, mo), tuple(choice)))
            return
        n = order[i]
        for exid, (cl, ce, cm) in comp[n].items():
            xl, xe, xm = ev.on_assign_raw(n, exid, assign)
            assign[n] = exid
            choice.append(exid)
            rec(i + 1, lat + cl + xl, en + ce + xe, mo + cm + xm)
            choice.pop()
            del assign[n]

    rec(0, 0.0, 0.0, 0.0)
    # Non-dominated filter: sort by latency, then keep what nothing before beats.
    found.sort()
    front: List[Tuple[Tuple[float, float, float], Tuple[str, ...]]] = []
    for v, a in found:
        if not any(o[0] <= v[0] and o[1] <= v[1] and o[2] <= v[2] and o != v for o, _ in front):
            front.append((v, a))
    w = policy.weights
    front.sort(key=lambda va: (va[0][0] * w.get("latency", 0.0) + va[0][1] * w.get("energy", 0.0)
                               + va[0][2] * w.get("money", 0.0), va[0]))
    plans = []
    seen: Set[str] = set()
    for _, a in front:
        p = _build_plan(graph, edges, order, cands, excluded, ev, dict(zip(order, a)), policy, "front")
        h = p.content_hash()
        if h not in seen:
            seen.add(h)
            plans.append(p)
    return Placement(plan=plans[0] if plans else None), tuple(plans)


# --- Observability: what actually ran, keyed by identity ---------------------------


class Observation(BaseModel):
    """A measured cost, attached to exactly the thing that ran. Decoration:
    cites a plan and an assignment (or a crossing) by identity; hashes into
    nothing below it. This is the record any aggregator folds over — a
    learned cost geometry needs a dataset, and this is its row."""

    model_config = ConfigDict(frozen=True)
    plan_hash: str = Field(min_length=1)
    subject: str = Field(min_length=1)  # a node_id, or "edge:<source>-><target>"
    executor_id: str = Field(min_length=1)
    realization: Optional[str] = None
    measured: CostVector
    source: str = Field(min_length=1)  # who measured, where, when — provenance
    peak_memory_bytes: Optional[float] = None

    def content_hash(self) -> str:
        return _content_hash(self.model_dump(mode="json"))


class Discrepancy(BaseModel):
    """Declared versus measured for one subject: the calibration signal.
    An executor's cost table is a *stated* claim; observations are the
    *empirical* one, and the gap between them is what recalibrates the
    advertisement (vault ADR-0003's grades, applied to cost)."""

    model_config = ConfigDict(frozen=True)
    subject: str
    executor_id: str
    declared: CostVector
    measured: CostVector
    ratio_latency: Optional[float]  # measured / declared, None if declared is zero


def reconcile(p: Plan, observations: Sequence[Observation], graph: PlacementGraph,
              executors: Sequence[Executor], policy: Policy) -> Tuple[Discrepancy, ...]:
    """Line up what the plan declared against what was observed for it.
    Observations for other plans are ignored (identity is the join key);
    subjects the plan never assigned are ignored too."""
    by_id = {ex.executor_id: ex for ex in executors}
    ev = _Evaluator(graph, graph.edge_list(), executors, policy)
    declared: Dict[Tuple[str, str], CostVector] = {}
    for a in p.assignments:
        declared[(a.node_id, a.executor_id)] = a.compute
    assign = {a.node_id: a.executor_id for a in p.assignments}
    for e in graph.edge_list():
        if assign[e.source] != assign[e.target]:
            declared[(f"edge:{e.source}->{e.target}", assign[e.target])] =                 policy.crossing(by_id[assign[e.source]], by_id[assign[e.target]], e.bytes)[1]
    out: List[Discrepancy] = []
    for o in observations:
        if o.plan_hash != p.content_hash():
            continue
        d = declared.get((o.subject, o.executor_id))
        if d is None:
            continue
        ratio = (o.measured.latency / d.latency) if d.latency else None
        out.append(Discrepancy(subject=o.subject, executor_id=o.executor_id,
                               declared=d, measured=o.measured, ratio_latency=ratio))
    return tuple(out)


# --- Reading the plan back ------------------------------------------------------


def data_flow(p: Plan, graph: PlacementGraph) -> Dict[str, Tuple[str, ...]]:
    """Where did my data go: every strip a job reads or writes, mapped to
    the executors that touched it. Read off the plan record, not the
    runtime — complete by construction."""
    touched: Dict[str, Set[str]] = {}
    for j in graph.jobs:
        ex = p.executor_of(j.node_id)
        for strip in (*j.reads, *j.writes):
            touched.setdefault(strip, set()).add(ex)
    return {strip: tuple(sorted(exs)) for strip, exs in sorted(touched.items())}


def verify_plan(p: Plan, graph: PlacementGraph, executors: Sequence[Executor],
                policy: Policy) -> Tuple[bool, str]:
    """Trust nothing recomputable: the graph and policy it names, the
    admission of every assignment, and every cost it claims — re-derived
    from the executor records and the policy, heuristics included."""
    if p.graph_hash != graph.content_hash():
        return False, "plan names a different graph"
    if p.policy_hash != policy.content_hash():
        return False, "plan names a different policy"
    by_id = {ex.executor_id: ex for ex in executors}
    assign = {a.node_id: a.executor_id for a in p.assignments}
    if set(assign) != {j.node_id for j in graph.jobs}:
        return False, "plan does not assign exactly the graph's nodes"
    jobs = {j.node_id: j for j in graph.jobs}
    compute = ZERO
    for a in p.assignments:
        ex = by_id.get(a.executor_id)
        if ex is None:
            return False, f"{a.node_id}: executor {a.executor_id!r} unknown"
        if not lock_admits(policy.lock_level, ex.locality):
            return False, f"{a.node_id}: {a.executor_id} is not admitted by lock {policy.lock_level!r}"
        j = jobs[a.node_id]
        if j.pinned_to is not None and j.pinned_to != a.executor_id:
            return False, f"{a.node_id}: pinned to {j.pinned_to!r} but placed on {a.executor_id!r}"
        expected = ex.cost_of(a.realization)
        if expected is None:
            return False, f"{a.node_id}: no cost declared for {a.realization!r} on {a.executor_id}"
        if expected != a.compute:
            return False, f"{a.node_id}: compute cost does not match the executor record"
        compute = compute + expected
    edges = graph.edge_list()
    ev = _Evaluator(graph, edges, executors, policy)
    boundary = ZERO
    heur = ZERO
    crossings = 0
    placed: Dict[str, str] = {}
    for n in _topological(graph, edges):
        b, hv, x, _ = ev.on_assign(n, assign[n], placed)
        placed[n] = assign[n]
        boundary = boundary + b
        heur = heur + hv
        crossings += x
    if compute != p.compute or boundary != p.boundary or heur != p.heuristic or crossings != p.crossings:
        return False, "plan's cost summary does not match its assignments"
    w = policy.weights
    folded = (compute.fold(w), boundary.fold(w), heur.fold(w))
    if any(abs(x - y) > 1e-9 for x, y in zip(folded, (p.compute_cost, p.boundary_cost, p.heuristic_cost))):
        return False, "plan's folded cost summary does not match its cost vectors under the policy's weights"
    if abs(sum(folded) - p.objective) > 1e-9:
        return False, "plan's objective does not fold from its costs under the policy's weights"
    return True, "graph, policy, assignments, costs, and heuristics re-derive consistently"


__all__ = [
    "Assignment", "BoundaryKind", "BoundaryPrice", "Candidate", "CostVector", "Edge",
    "Executor", "HEURISTICS", "HeuristicHit", "Job", "Locality", "LockLevel", "Method",
    "Discrepancy", "Observation", "Placement", "PlacementGraph", "Plan", "Policy",
    "boundary_kind", "candidates", "data_flow", "lock_admits", "plan", "plan_front",
    "reconcile", "verify_plan",
]
