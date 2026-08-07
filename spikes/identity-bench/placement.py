"""E7 — binding granularity: is per-ingot choice good enough?

Crossing a domain boundary (host<->GPU, CPU<->DSP, machine<->machine) costs
latency, and often costs *precision* too: f64 on the CPU, f32 on the GPU,
fixed-point on the DSP. So choosing the best casting for each ingot
independently ignores the edges, and the edges can dominate.

Exhaustive over 3^N assignments — no heuristic to defend, these are exact
optima.
"""

from __future__ import annotations

import itertools
from dataclasses import dataclass

DOMAINS = ("cpu", "gpu", "dsp")

# Per-node compute cost (ms) by domain. `None` = no casting exists there, so
# the ingot's portable form is the only option (modelled as the cpu cost).
NODE_COSTS = [
    #  cpu   gpu   dsp
    (40.0, 10.0, None),   # 0 decode        — GPU-friendly
    (30.0, 6.0, None),    # 1 resize        — GPU-friendly
    (8.0, 30.0, 3.0),     # 2 band-filter   — DSP wins big
    (6.0, 25.0, 2.0),     # 3 envelope      — DSP wins big
    (50.0, 8.0, None),    # 4 embed         — GPU wins big
    (5.0, 20.0, None),    # 5 match         — small, CPU fine
    (4.0, 18.0, None),    # 6 track-update  — small, CPU fine
    (3.0, 15.0, None),    # 7 emit          — small, CPU fine
]

# Cost (ms) to move a value across a boundary. Symmetric here for simplicity.
CROSS = {
    ("cpu", "cpu"): 0.0, ("gpu", "gpu"): 0.0, ("dsp", "dsp"): 0.0,
    ("cpu", "gpu"): 12.0, ("gpu", "cpu"): 12.0,
    ("cpu", "dsp"): 8.0, ("dsp", "cpu"): 8.0,
    ("gpu", "dsp"): 20.0, ("dsp", "gpu"): 20.0,
}

# Relative deviation contributed by running a node on a given domain, versus
# the ingot's reference (f64 on the CPU). This is E3/E5's tolerance budget,
# spent at the same places latency is spent.
DEVIATION = {"cpu": 0.0, "gpu": 1e-7, "dsp": 1e-3}


@dataclass(frozen=True)
class Plan:
    assign: tuple
    latency: float
    crossings: int
    deviation: float

    def regions(self) -> int:
        r = 1
        for a, b in zip(self.assign, self.assign[1:]):
            if a != b:
                r += 1
        return r

    def shape(self) -> str:
        return "-".join(d[0].upper() for d in self.assign)


def _cost(node: int, domain: str) -> float | None:
    c = NODE_COSTS[node][DOMAINS.index(domain)]
    return c


def evaluate(assign: tuple, cross=None) -> Plan | None:
    cross = cross or CROSS
    total = 0.0
    dev = 0.0
    crossings = 0
    for i, d in enumerate(assign):
        c = _cost(i, d)
        if c is None:
            return None  # no casting exists on that domain
        total += c
        dev += DEVIATION[d]
        if i:
            prev = assign[i - 1]
            if prev != d:
                crossings += 1
                total += cross[(prev, d)]
    return Plan(assign, total, crossings, dev)


def all_plans(cross=None) -> list[Plan]:
    out = []
    for assign in itertools.product(DOMAINS, repeat=len(NODE_COSTS)):
        p = evaluate(assign, cross)
        if p is not None:
            out.append(p)
    return out


def greedy_per_node() -> Plan:
    """Pick the cheapest domain for each ingot independently — ignores edges.

    This is what "rank and choose at ingot granularity" actually means, and it
    is the thing under test.
    """
    assign = []
    for i in range(len(NODE_COSTS)):
        best_d, best_c = None, float("inf")
        for d in DOMAINS:
            c = _cost(i, d)
            if c is not None and c < best_c:
                best_d, best_c = d, c
        assign.append(best_d)
    return evaluate(tuple(assign))


def optimal_latency(cross=None) -> Plan:
    return min(all_plans(cross), key=lambda p: p.latency)


def pareto(plans: list[Plan]) -> list[Plan]:
    """Non-dominated over (latency, deviation) — lower is better on both."""
    front = []
    for p in plans:
        if not any(
            (q.latency <= p.latency and q.deviation <= p.deviation)
            and (q.latency < p.latency or q.deviation < p.deviation)
            for q in plans
        ):
            front.append(p)
    # dedupe by (latency, deviation)
    seen, out = set(), []
    for p in sorted(front, key=lambda p: (p.latency, p.deviation)):
        k = (round(p.latency, 6), round(p.deviation, 12))
        if k not in seen:
            seen.add(k)
            out.append(p)
    return out


def scaled_cross(factor: float) -> dict:
    return {k: v * factor for k, v in CROSS.items()}
