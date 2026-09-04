"""Descriptions and the satisfies judgment (PR-d1).

A *description* says what a job is — never how. A *realization* (today: a
node type, identified by a string handle; tomorrow: a content hash) may
*claim* to satisfy one, and binding checks the claim in two judgments
(vault ADR-0021, measured in `spikes/matching-bench/`):

  valence check — structural. Does the realization's declared strip
      interface bond the way the description requires? Decided from the
      `NodeSpec` alone. This is what *consumes* `reads_strip_patterns` and
      `writes_strips` — a declaration nothing reads decays into decoration
      within a release, so here is the reader.
  assay — empirical. Score the realization's output against the
      description's shipped reference with the description's own metric
      (`assay_metrics.py`). The metric ships with the description because
      deviation is job-shaped. The assay certifies the probed subset, never
      a universal claim, so the receipt carries probe provenance.

Claims and hallmarks (assay receipts) are DECORATION (ADR-0022): they cite
a realization and a description by identity, and nothing functional may
reference them back — attaching a claim never perturbs the identity of the
thing claimed about. The direction is enforced by `assert_functional`,
which rejects any node reading or writing a decoration namespace.

This module judges; it does not execute. Running a realization over probe
inputs is the scheduler's job; `run_assay` takes the outputs.
"""

from __future__ import annotations

import hashlib
import json
from typing import Any, Dict, List, Literal, Mapping, Optional, Sequence, Tuple

from pydantic import BaseModel, ConfigDict, Field

from nodecules.core.assay_metrics import score
from nodecules.core.strip_access import AccessPattern, RangePattern
from nodecules.core.types import NodeSpec

# Strip namespaces that hold decoration — labels *about* nodes. A functional
# node may never read or write these; see `assert_functional`.
DECORATION_NAMESPACES: Tuple[str, ...] = ("claims/", "hallmarks/", "vouches/")

ProbeProvenance = Literal["fixed-suite", "fresh-drawn", "workload"]
Outcome = Literal["exact", "via-substitute"]


def _content_hash(payload: Any) -> str:
    canonical = json.dumps(payload, sort_keys=True, separators=(",", ":"))
    return hashlib.sha256(canonical.encode("utf-8")).hexdigest()


# --- The description --------------------------------------------------------


class StripRequirement(BaseModel):
    """One input the job consumes: a strip, read with a given pattern."""

    model_config = ConfigDict(frozen=True)
    strip_name: str = Field(min_length=1)
    pattern: AccessPattern


class ProducedStrip(BaseModel):
    """One output the job produces: a strip whose records follow a schema.

    `schema_id` is a name today (`stenota.core.models.ASRSegment`) and a
    content hash once schemas are nodes — either way it is part of the
    description's identity, per interface immutability.
    """

    model_config = ConfigDict(frozen=True)
    strip_name: str = Field(min_length=1)
    schema_id: str = Field(min_length=1)


class Tolerance(BaseModel):
    """The acceptance line: `metric(candidate, reference) <= max_value`.

    A scalar for now — the margin-relative replacement (vault P-28) arrives
    when decision margins exist as strips. Metrics are errors: lower is
    better, 0.0 is identical.
    """

    model_config = ConfigDict(frozen=True)
    metric: str = Field(min_length=1)
    max_value: float = Field(ge=0.0)

    def passes(self, measured: float) -> bool:
        return measured <= self.max_value


class Description(BaseModel):
    """What a job is: its valence, its acceptance line, and the realization
    it ships as reference (the ingot's role, vault ADR-0014).

    Identity (`content_hash`) covers consumes / produces / tolerance /
    reference — editing any of them makes a different description. The
    `name` is a human alias and is deliberately *not* hashed: two names for
    one contract are one contract.
    """

    model_config = ConfigDict(frozen=True)
    name: str = Field(min_length=1)
    consumes: Tuple[StripRequirement, ...]
    produces: Tuple[ProducedStrip, ...] = Field(min_length=1)
    tolerance: Tolerance
    reference: str = Field(min_length=1)

    def content_hash(self) -> str:
        payload = self.model_dump(mode="json", exclude={"name"})
        return _content_hash(payload)


# --- Decoration: claims and hallmarks ---------------------------------------


class SatisfiesClaim(BaseModel):
    """A realization's assertion that it satisfies a description.

    Decoration: cites both by identity, hashes into neither. Stated
    decoration (grade, cost class, locality) lives here — on the claim,
    not the description — because it describes the *realization's* offer
    (keyhole: executors declare contracts in `hello`, with grade and cost).
    """

    model_config = ConfigDict(frozen=True)
    realization: str = Field(min_length=1)
    description_hash: str = Field(min_length=1)
    claimant: str = Field(min_length=1)
    grade: Optional[str] = None
    cost_class: Optional[str] = None
    locality: Optional[str] = None
    evidence: Tuple[str, ...] = ()  # hallmark hashes backing the claim


class Hallmark(BaseModel):
    """An assay receipt (vault ADR-0019's hallmark payload, unsigned).

    Every field is derivable from content or names how it was produced:
    `outcome` follows from `realization == reference`; `measured` can be
    re-run; `probe_provenance` says whether the probes were a published
    suite (Goodhart-void as evidence — matching-bench M7), drawn fresh by
    the assayer, or sampled from the live workload.
    """

    model_config = ConfigDict(frozen=True)
    realization: str = Field(min_length=1)
    description_hash: str = Field(min_length=1)
    reference: str = Field(min_length=1)
    outcome: Outcome
    metric: str = Field(min_length=1)
    measured: float = Field(ge=0.0)
    max_value: float = Field(ge=0.0)
    n_probes: int = Field(ge=1)
    probe_provenance: ProbeProvenance
    probe_seed: Optional[str] = None

    @staticmethod
    def derive_outcome(realization: str, reference: str) -> Outcome:
        return "exact" if realization == reference else "via-substitute"

    def content_hash(self) -> str:
        return _content_hash(self.model_dump(mode="json"))


# --- The valence check (structural) -----------------------------------------


def _pattern_compatible(required: AccessPattern, declared: AccessPattern) -> bool:
    if required.kind != declared.kind:
        return False
    if isinstance(required, RangePattern) and isinstance(declared, RangePattern):
        return required.field == declared.field
    return True


def valence_check(spec: NodeSpec, desc: Description) -> List[str]:
    """Structural judgment: does this realization's declared interface bond
    the way the description requires? Returns problems; empty means it
    passes. Cannot distinguish an impostor with an identical interface —
    that is the assay's job."""
    problems: List[str] = []
    if desc.consumes and not spec.reads_strip_patterns:
        problems.append(
            f"{spec.node_type} declares no strip access patterns; not statically checkable"
        )
    declared = {sa.strip_name: sa.pattern for sa in spec.reads_strip_patterns}
    for req in desc.consumes:
        if req.strip_name not in declared:
            problems.append(f"{spec.node_type} does not read {req.strip_name!r}")
        elif not _pattern_compatible(req.pattern, declared[req.strip_name]):
            problems.append(
                f"{spec.node_type} reads {req.strip_name!r} as "
                f"{declared[req.strip_name].kind!r}, description requires {req.pattern.kind!r}"
            )
    writes = set(spec.writes_strips)
    for out in desc.produces:
        if out.strip_name not in writes:
            problems.append(f"{spec.node_type} does not write {out.strip_name!r}")
    return problems


def assert_functional(spec: NodeSpec) -> None:
    """Enforce the decoration direction: a functional node may never read or
    write a decoration namespace. A claim or hallmark cites the node; the
    node never cites it back — otherwise attaching a label would perturb
    the identity of the thing labelled (vault ADR-0022)."""
    touched = [
        *spec.reads_strips,
        *(sa.strip_name for sa in spec.reads_strip_patterns),
        *spec.writes_strips,
    ]
    for name in touched:
        for ns in DECORATION_NAMESPACES:
            if name.startswith(ns):
                raise ValueError(
                    f"{spec.node_type} references decoration strip {name!r}: "
                    "functional nodes may not bond to claims, hallmarks, or vouches"
                )


# --- The assay (empirical) and the decision ---------------------------------


class AssayResult(BaseModel):
    """What one assay measured, with the receipt it issued."""

    model_config = ConfigDict(frozen=True)
    realization: str
    measured: float
    cost_s: float = Field(ge=0.0)
    hallmark: Hallmark


def run_assay(
    desc: Description,
    realization: str,
    candidate_output: object,
    reference_output: object,
    *,
    n_probes: int,
    probe_provenance: ProbeProvenance,
    probe_seed: Optional[str] = None,
    cost_s: float = 0.0,
) -> AssayResult:
    """Score a realization's output against the reference's with the
    description's metric and issue the hallmark. Execution happened
    elsewhere; this judges what came out."""
    measured = score(desc.tolerance.metric, candidate_output, reference_output)
    hallmark = Hallmark(
        realization=realization,
        description_hash=desc.content_hash(),
        reference=desc.reference,
        outcome=Hallmark.derive_outcome(realization, desc.reference),
        metric=desc.tolerance.metric,
        measured=measured,
        max_value=desc.tolerance.max_value,
        n_probes=n_probes,
        probe_provenance=probe_provenance,
        probe_seed=probe_seed,
    )
    return AssayResult(
        realization=realization, measured=measured, cost_s=cost_s, hallmark=hallmark
    )


class Binding(BaseModel):
    """The decision. `chosen is None` is an answer, not an error: the query
    failed ordinarily (no claim survived both judgments)."""

    model_config = ConfigDict(frozen=True)
    description_hash: str
    chosen: Optional[AssayResult] = None
    rejected: Dict[str, str] = Field(default_factory=dict)  # realization -> why


def decide(
    desc: Description,
    claims: Sequence[SatisfiesClaim],
    specs: Mapping[str, NodeSpec],
    assays: Mapping[str, AssayResult],
) -> Binding:
    """Bind a description: claims name the candidates; the valence check
    prunes them; the assay filters them; the cheapest survivor wins.

    Cost preference alone would preferentially select impostors (the
    cheapest structurally-valid plan in the bench was the wrong one), so
    the assay is applied before cost is consulted, never after.
    """
    want = desc.content_hash()
    rejected: Dict[str, str] = {}
    passing: List[AssayResult] = []
    for claim in claims:
        if claim.description_hash != want:
            continue
        r = claim.realization
        spec = specs.get(r)
        if spec is None:
            rejected[r] = "no NodeSpec available for the claimed realization"
            continue
        problems = valence_check(spec, desc)
        if problems:
            rejected[r] = "valence: " + "; ".join(problems)
            continue
        assay = assays.get(r)
        if assay is None:
            rejected[r] = "no assay result; unmeasured realizations are not bound"
            continue
        if not desc.tolerance.passes(assay.measured):
            rejected[r] = (
                f"assay: {desc.tolerance.metric}={assay.measured:.4g} "
                f"exceeds {desc.tolerance.max_value:.4g}"
            )
            continue
        passing.append(assay)
    if not passing:
        return Binding(description_hash=want, rejected=rejected)
    chosen = min(passing, key=lambda a: a.cost_s)
    return Binding(description_hash=want, chosen=chosen, rejected=rejected)


def verify_hallmark(
    hallmark: Hallmark, desc: Description, *, remeasured: float
) -> Tuple[bool, str]:
    """An independent check that trusts nothing in the receipt it can
    recompute: the description it names, the outcome the hashes imply,
    and whether a fresh measurement still passes."""
    if hallmark.description_hash != desc.content_hash():
        return False, "receipt was issued against a different description"
    if hallmark.reference != desc.reference:
        return False, "receipt names a different reference realization"
    expected = Hallmark.derive_outcome(hallmark.realization, desc.reference)
    if hallmark.outcome != expected:
        return False, f"outcome mismatch: hashes say {expected!r}, receipt says {hallmark.outcome!r}"
    if not desc.tolerance.passes(remeasured):
        return False, (
            f"re-measured {desc.tolerance.metric}={remeasured:.4g} exceeds "
            f"{desc.tolerance.max_value:.4g}"
        )
    return True, "hashes, outcome, and re-measurement consistent"


__all__ = [
    "DECORATION_NAMESPACES",
    "AssayResult",
    "Binding",
    "Description",
    "Hallmark",
    "Outcome",
    "ProbeProvenance",
    "ProducedStrip",
    "SatisfiesClaim",
    "StripRequirement",
    "Tolerance",
    "assert_functional",
    "decide",
    "run_assay",
    "valence_check",
    "verify_hallmark",
]
