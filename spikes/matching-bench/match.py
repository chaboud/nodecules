"""Matching: the structural search, the assay, the decision, the check.

The claim under test: the satisfies judgment decomposes into two judgments
with different characters —

  valence check (structural) — do the plan's ends and internal bonds line
      up with the description's kinds? Decidable from interfaces alone;
      cannot tell a casting from an impostor.
  assay (empirical) — run the candidate against the description's
      reference realization on sampled inputs and measure deviation
      (ADR-0014's conformance oracle; the resolution of P-12).

The decision is then policy over the survivors (here: cheapest passing
plan, the dumbest matcher P-11 asks for), and the receipt binds what
actually ran to hashes an independent verifier can re-check — ADR-0019's
hallmark payload, minus the signature (no fake crypto in a spike).
"""

from __future__ import annotations

import time
from dataclasses import dataclass
from typing import Optional, Sequence

from descriptions import Description, Realization, plan_hash

Plan = tuple[Realization, ...]

OUTCOME_EXACT = "exact"
OUTCOME_SUBSTITUTE = "via-substitute"


def run_plan(plan: Plan, x):
    for r in plan:
        x = r.fn(x)
    return x


# --- 1. The valence check: structural search over the inventory -------------


def find_plans(desc: Description, inventory: Sequence[Realization],
               max_depth: int = 3) -> list[Plan]:
    """Every acyclic chain through the inventory whose ends match the
    description's kinds. This is the whole structural judgment: it knows
    interfaces and nothing else."""
    plans: list[Plan] = []

    def extend(chain: list[Realization], at_kind: str) -> None:
        if chain and at_kind == desc.produces:
            plans.append(tuple(chain))
            return
        if len(chain) >= max_depth:
            return
        for r in inventory:
            if r.consumes == at_kind and r not in chain:
                extend(chain + [r], r.produces)

    extend([], desc.consumes)
    return plans


# --- 2. The assay: measured deviation against the reference ----------------


@dataclass(frozen=True)
class Assay:
    plan: Plan
    graph_hash: str
    worst: float
    mean: float
    cost_s: float  # wall time for the sample sweep — the cost signal

    @property
    def names(self) -> str:
        return " -> ".join(r.name for r in self.plan)


def assay(plan: Plan, reference_outputs: list, samples: list) -> Assay:
    t0 = time.perf_counter()
    outputs = [run_plan(plan, s) for s in samples]
    elapsed = time.perf_counter() - t0

    worst = 0.0
    total = 0.0
    for got, want in zip(outputs, reference_outputs):
        d = max(abs(a - b) for a, b in zip(got, want))
        total += d
        if d > worst:
            worst = d
    return Assay(
        plan=plan,
        graph_hash=plan_hash(plan),
        worst=worst,
        mean=total / len(samples),
        cost_s=elapsed,
    )


# --- 3. The decision, and the receipt it must sign for ---------------------


@dataclass(frozen=True)
class Receipt:
    """ADR-0019's hallmark payload, unsigned: what ran, bound to hashes."""

    plan_names: tuple[str, ...]
    graph_hash: str
    reference_hash: str
    outcome: str
    worst_deviation: float
    tolerance: float
    n_samples: int


@dataclass(frozen=True)
class Binding:
    chosen: Optional[Assay]
    receipt: Optional[Receipt]
    assays: tuple[Assay, ...]  # every candidate, for reporting


def decide(desc: Description, inventory: Sequence[Realization],
           samples: list) -> Binding:
    """The worker: handed a description and its own inventory, produce a
    graph — or fail ordinarily (executors.md: query must be able to fail;
    E_NOINTERFACE is why QI means something)."""
    reference_outputs = [run_plan(desc.reference, s) for s in samples]
    ref_hash = plan_hash(desc.reference)

    candidates = find_plans(desc, inventory)
    assays = tuple(assay(p, reference_outputs, samples) for p in candidates)
    passing = [a for a in assays if a.worst <= desc.tolerance]
    if not passing:
        return Binding(chosen=None, receipt=None, assays=assays)

    chosen = min(passing, key=lambda a: a.cost_s)
    outcome = OUTCOME_EXACT if chosen.graph_hash == ref_hash else OUTCOME_SUBSTITUTE
    receipt = Receipt(
        plan_names=tuple(r.name for r in chosen.plan),
        graph_hash=chosen.graph_hash,
        reference_hash=ref_hash,
        outcome=outcome,
        worst_deviation=chosen.worst,
        tolerance=desc.tolerance,
        n_samples=len(samples),
    )
    return Binding(chosen=chosen, receipt=receipt, assays=assays)


# --- 4. The hallmark check: independent re-verification ---------------------


def verify(receipt: Receipt, claimed_plan: Plan, desc: Description,
           samples: list) -> tuple[bool, str]:
    """ADR-0019 commitment 2: anyone who can obtain the content can re-run
    and compare. The verifier trusts nothing in the receipt — it recomputes
    the hash from the plan it was handed, re-derives the outcome, and
    re-measures deviation on its own samples."""
    recomputed = plan_hash(claimed_plan)
    if recomputed != receipt.graph_hash:
        return False, "graph hash mismatch: the plan is not the graph the receipt names"

    ref_hash = plan_hash(desc.reference)
    if ref_hash != receipt.reference_hash:
        return False, "reference hash mismatch: receipt was issued against a different description"

    expected_outcome = OUTCOME_EXACT if recomputed == ref_hash else OUTCOME_SUBSTITUTE
    if expected_outcome != receipt.outcome:
        return False, f"outcome mismatch: hashes say {expected_outcome!r}, receipt says {receipt.outcome!r}"

    reference_outputs = [run_plan(desc.reference, s) for s in samples]
    a = assay(claimed_plan, reference_outputs, samples)
    if a.worst > receipt.tolerance:
        return False, f"deviation {a.worst:.3e} exceeds the tolerance {receipt.tolerance:.1e} the receipt claims"

    return True, f"re-ran {len(samples)} samples: worst {a.worst:.3e} within tolerance; hashes and outcome consistent"
