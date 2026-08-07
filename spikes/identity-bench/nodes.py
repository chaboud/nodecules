"""Bench nodes: colour conversion, a perturbing set, and an IIR filter.

The colour chain is the founder's toy for ADR-0010: a worker holding a fused
RGB->YUV may semantically shorten RGB->HSL->YUV. Both paths are correct; the
question is whether they are *identical*, and the bench measures it.
"""

from __future__ import annotations

import math
import os
import random
import time

# --- BT.601 constants ------------------------------------------------------

_Y = (0.299, 0.587, 0.114)
_U = (-0.14713, -0.28886, 0.436)
_V = (0.615, -0.51499, -0.10001)


def rgb_to_yuv(rgb: tuple[float, float, float]) -> tuple[float, float, float]:
    """Direct BT.601 RGB -> YUV. The fused node."""
    r, g, b = rgb
    return (
        _Y[0] * r + _Y[1] * g + _Y[2] * b,
        _U[0] * r + _U[1] * g + _U[2] * b,
        _V[0] * r + _V[1] * g + _V[2] * b,
    )


def rgb_to_hsl(rgb: tuple[float, float, float]) -> tuple[float, float, float]:
    """RGB -> HSL. Hue in turns [0,1), saturation and lightness in [0,1]."""
    r, g, b = rgb
    hi, lo = max(r, g, b), min(r, g, b)
    lightness = (hi + lo) / 2.0
    if hi == lo:
        return (0.0, 0.0, lightness)
    delta = hi - lo
    sat = delta / (2.0 - hi - lo) if lightness > 0.5 else delta / (hi + lo)
    if hi == r:
        hue = ((g - b) / delta) % 6.0
    elif hi == g:
        hue = (b - r) / delta + 2.0
    else:
        hue = (r - g) / delta + 4.0
    return (hue / 6.0, sat, lightness)


def hsl_to_yuv(hsl: tuple[float, float, float]) -> tuple[float, float, float]:
    """HSL -> YUV, realistically implemented as HSL -> RGB -> YUV.

    This is the honest shape of a real intermediate node: nobody writes a
    direct HSL->YUV, they route through RGB. That round trip is where the
    numeric difference against the fused path comes from.
    """
    h, s, lightness = hsl
    if s == 0.0:
        r = g = b = lightness
    else:
        q = lightness * (1 + s) if lightness < 0.5 else lightness + s - lightness * s
        p = 2 * lightness - q

        def _hue_to_rgb(t: float) -> float:
            t = t % 1.0
            if t < 1 / 6:
                return p + (q - p) * 6 * t
            if t < 1 / 2:
                return q
            if t < 2 / 3:
                return p + (q - p) * (2 / 3 - t) * 6
            return p

        r = _hue_to_rgb(h + 1 / 3)
        g = _hue_to_rgb(h)
        b = _hue_to_rgb(h - 1 / 3)
    return rgb_to_yuv((r, g, b))


# --- Perturbing nodes, for the classifier to find --------------------------


def stamp_now(x: float) -> tuple[float, float]:
    """PERTURBING: reads a clock."""
    return (x, time.time())


def jitter(x: float) -> float:
    """PERTURBING: unseeded RNG."""
    return x + random.random()


def read_env_scale(x: float) -> float:
    """PERTURBING: reads process environment — the classic uncovered input."""
    return x * float(os.environ.get("BENCH_SCALE", "1.0"))


def seeded_jitter(x: float, seed: int) -> float:
    """PURE with respect to a declared seed — ADR-0009's escape hatch."""
    rng = random.Random(seed)
    return x + rng.random()


def scale(x: float, k: float) -> float:
    """PURE: output is a function of declared inputs alone."""
    return x * k


# --- IIR / FIR, for the retention-window measurement -----------------------


def iir_lowpass(samples: list[float], alpha: float, state: float = 0.0) -> tuple[list[float], float]:
    """One-pole IIR: y[n] = a*x[n] + (1-a)*y[n-1].

    Returns (outputs, final_state). The state is the whole point: without it,
    output at n depends on every sample before n, forever.
    """
    out = []
    y = state
    for x in samples:
        y = alpha * x + (1.0 - alpha) * y
        out.append(y)
    return out, y


def fir_lowpass(samples: list[float], taps: int) -> list[float]:
    """Bounded-memory FIR: a plain moving average over `taps` samples.

    Contrast with the IIR: output at n depends on exactly `taps` prior
    samples, so a window of that size fully bounds the retention envelope.
    """
    out = []
    for i in range(len(samples)):
        lo = max(0, i - taps + 1)
        window = samples[lo : i + 1]
        out.append(sum(window) / len(window))
    return out


def iir_error_horizon(alpha: float, tolerance: float) -> int:
    """How many samples until a wrong initial state decays below `tolerance`.

    Error decays as (1-alpha)^n, so n = log(tol)/log(1-alpha). This is the
    closed form for "how far back must I retain if I did NOT checkpoint."
    """
    return math.ceil(math.log(tolerance) / math.log(1.0 - alpha))
