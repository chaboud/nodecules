"""The description shape — the intensional half of the satisfies judgment.

Spike, not core. Feeds unification-plan item 5 (the satisfies judgment as
nodes) by grounding the smallest description that can drive real matching:
what must a worker be handed so that "does this concrete plan do what that
description says?" is checkable rather than asserted.

A description carries four things:

  consumes / produces — kind names. The structural half of the judgment:
      the bonds a plan must form at its ends (a kind's valence, in the
      ADR-0020 vocabulary).
  tolerance — the maximum per-element deviation from the reference that
      the description's author will accept. A scalar stand-in for the
      margin distribution of P-28; see README honest scope.
  reference — the realization the description ships with (the ingot's
      role, ADR-0014). Deviation is measured against it, and it is the
      fallback when no cheaper plan passes.

The description is itself content-addressed: its identity covers the
interface, the tolerance, and the reference chain's composed hash — so
editing any of them makes a different description, per the interface-
immutability rule (executors.md: new capability is a new interface).

The toy chain is shared with ../identity-bench (same founder toy, ADR-0010);
both spikes retire together when their findings are absorbed.
"""

from __future__ import annotations

import hashlib
import json
import sys
from dataclasses import dataclass
from pathlib import Path
from typing import Callable

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "identity-bench"))

import cas  # noqa: E402
import nodes  # noqa: E402


@dataclass(frozen=True)
class Realization:
    """One concrete unit of compute in a worker's inventory."""

    fn: Callable
    consumes: str
    produces: str

    @property
    def name(self) -> str:
        return self.fn.__name__


@dataclass(frozen=True)
class Description:
    """What the requester wants, and nothing about how."""

    consumes: str
    produces: str
    tolerance: float
    reference: tuple[Realization, ...]


def plan_hash(plan: tuple[Realization, ...]) -> str:
    """Composed graph hash of a concrete plan — ADR-0003's lower layer."""
    composed, _ = cas.build_chain([r.fn for r in plan])
    return composed


def description_hash(desc: Description) -> str:
    """A description is a node too; its hash covers interface + tolerance +
    the reference realization it ships with."""
    payload = json.dumps(
        {
            "consumes": desc.consumes,
            "produces": desc.produces,
            "tolerance": repr(desc.tolerance),
            "reference": plan_hash(desc.reference),
        },
        sort_keys=True,
        separators=(",", ":"),
    )
    return hashlib.sha256(payload.encode("utf-8")).hexdigest()


# --- The impostor, and a dead end ------------------------------------------

_Y709 = (0.2126, 0.7152, 0.0722)
_U709 = (-0.09991, -0.33609, 0.436)
_V709 = (0.615, -0.55861, -0.05639)


def rgb_to_yuv_bt709(rgb: tuple[float, float, float]) -> tuple[float, float, float]:
    """The impostor: same valence as the BT.601 casting — consumes color.rgb,
    produces color.yuv — and a different answer (BT.709 primaries). Interface
    matching alone cannot tell it from rgb_to_yuv; only the assay can."""
    r, g, b = rgb
    return (
        _Y709[0] * r + _Y709[1] * g + _Y709[2] * b,
        _U709[0] * r + _U709[1] * g + _U709[2] * b,
        _V709[0] * r + _V709[1] * g + _V709[2] * b,
    )


def luma_only(rgb: tuple[float, float, float]) -> float:
    """A dead end for the structural search: right input kind, wrong output."""
    r, g, b = rgb
    return 0.299 * r + 0.587 * g + 0.114 * b


# --- Standard fixtures ------------------------------------------------------

R_RGB_TO_HSL = Realization(nodes.rgb_to_hsl, "color.rgb", "color.hsl")
R_HSL_TO_YUV = Realization(nodes.hsl_to_yuv, "color.hsl", "color.yuv")
R_FUSED = Realization(nodes.rgb_to_yuv, "color.rgb", "color.yuv")
R_IMPOSTOR = Realization(rgb_to_yuv_bt709, "color.rgb", "color.yuv")
R_LUMA = Realization(luma_only, "color.rgb", "color.luma")

REFERENCE_CHAIN = (R_RGB_TO_HSL, R_HSL_TO_YUV)


def standard_inventory() -> tuple[Realization, ...]:
    return (R_RGB_TO_HSL, R_HSL_TO_YUV, R_FUSED, R_IMPOSTOR, R_LUMA)


def describe(tolerance: float) -> Description:
    return Description(
        consumes="color.rgb",
        produces="color.yuv",
        tolerance=tolerance,
        reference=REFERENCE_CHAIN,
    )
