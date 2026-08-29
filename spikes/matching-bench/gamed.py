"""Adversarial realizations for P-32: gaming the assay.

The assay certifies a use-case subset (ADR-0021). The attack that follows is
exactly Goodhart's: behave on the probed subset, defect off it — the
emissions-defeat shape. Two adversaries here:

  make_defeat_device(probe_set)
      Honest on every input in the known probe set, wrong (BT.709)
      everywhere else. Models a realization tuned against a *published*
      conformance suite. On the suite it deviates exactly as much as the
      honest casting (~ULP scale) — indistinguishable by any probe in it.

  make_sparse_defector(probe_set, defect_fraction)
      Honest on the probe set AND on most other inputs; defects only on a
      deterministic pseudo-random fraction f of the input space. Models the
      harder target: rare defection. Detection by a fresh probe is a
      per-sample Bernoulli(f) event, which is what M8 measures.

Defection is keyed on `hash(rgb)` — deterministic for float tuples across
runs and processes (PYTHONHASHSEED salts str/bytes only), so the bench is
reproducible.
"""

from __future__ import annotations

from typing import Callable, FrozenSet

import descriptions as d

RGB = tuple[float, float, float]

_DENOM = float(2**32)


def _u01(rgb: RGB) -> float:
    """Deterministic per-input uniform in [0, 1)."""
    return (hash(rgb) & 0xFFFFFFFF) / _DENOM


def make_defeat_device(probe_set: FrozenSet[RGB]) -> Callable[[RGB], RGB]:
    def rgb_to_yuv_defeat(rgb: RGB) -> RGB:
        """Gamed casting: honest exactly where it expects to be probed."""
        if rgb in probe_set:
            return d.nodes.rgb_to_yuv(rgb)
        return d.rgb_to_yuv_bt709(rgb)

    return rgb_to_yuv_defeat


def make_sparse_defector(probe_set: FrozenSet[RGB],
                         defect_fraction: float) -> Callable[[RGB], RGB]:
    def rgb_to_yuv_sparse(rgb: RGB) -> RGB:
        """Gamed casting: honest on probes and on most inputs; defects on a
        deterministic fraction of the space."""
        if rgb not in probe_set and _u01(rgb) < defect_fraction:
            return d.rgb_to_yuv_bt709(rgb)
        return d.nodes.rgb_to_yuv(rgb)

    return rgb_to_yuv_sparse
