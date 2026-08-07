"""E5 — does a tiny perturbation stay tiny in a discrete-stateful node?

The colour case (E3) is stateless: two semantically-equivalent graphs differ by
~1 ULP per sample and the difference never accumulates. Face re-identification
is the same *kind* of substitution — two matchers, same declared intent, not
precise replacements — but it is *discrete* and *stateful*: it assigns an
observation to a track and then updates that track's centroid.

Question: does a ULP-scale difference between two otherwise-identical matchers
stay ULP-scale, or does it flip a near-threshold decision and diverge
permanently?

No ML here. A deliberately simple nearest-centroid tracker with an EMA update,
which is the shape of the real thing without the weights.
"""

from __future__ import annotations

import math
import random


def _cos_dist(a: list[float], b: list[float]) -> float:
    dot = sum(x * y for x, y in zip(a, b))
    na = math.sqrt(sum(x * x for x in a))
    nb = math.sqrt(sum(y * y for y in b))
    if na == 0.0 or nb == 0.0:
        return 1.0
    return 1.0 - dot / (na * nb)


def make_stream(n_people: int, n_frames: int, dim: int, sigma: float, seed: int):
    """Observations of `n_people` people, each a fixed centroid plus noise.

    Returns (observations, truth_ids). sigma is tuned so that some frames land
    genuinely near the decision boundary — which is where the interesting
    behaviour lives, and where real re-id spends its errors too.
    """
    rng = random.Random(seed)
    centroids = [[rng.gauss(0, 1) for _ in range(dim)] for _ in range(n_people)]
    obs, truth = [], []
    for i in range(n_frames):
        p = rng.randrange(n_people)
        obs.append([centroids[p][d] + rng.gauss(0, sigma) for d in range(dim)])
        truth.append(p)
    return obs, truth


def _independent_wobble(ob, c, scale: float) -> float:
    """A deterministic, per-(observation, centroid) relative perturbation.

    This is the load-bearing detail. A *uniform* `d * (1 + jitter)` is a
    monotone transform: it preserves the ordering of distances exactly, so it
    can never flip an argmin, and a first version of this experiment showed
    100% agreement at every scale as a result. That is a real finding in
    itself — an implementation difference that perturbs every comparison
    identically is safe by construction.

    A genuinely different implementation does not do that. Different summation
    order, a different SIMD width, a different embedding model: each comparison
    lands slightly differently and *independently*. That is what can reorder
    two near-equal candidates. Derived from the values so it stays
    reproducible.
    """
    if scale == 0.0:
        return 0.0
    import hashlib
    key = f"{ob[0]:.12e}|{c[0]:.12e}|{ob[-1]:.12e}|{c[-1]:.12e}"
    h = int(hashlib.sha256(key.encode()).hexdigest()[:8], 16)
    return scale * (((h % 2000) / 1000.0) - 1.0)  # in [-scale, +scale]


def track(observations, tau: float, alpha: float = 0.3, jitter: float = 0.0):
    """Greedy nearest-centroid tracker with an EMA centroid update.

    `jitter` bounds a per-comparison independent relative perturbation standing
    in for "a different but semantically equivalent implementation".

    Returns (assignments, n_tracks).
    """
    centroids: list[list[float]] = []
    assignments: list[int] = []
    for ob in observations:
        best_i, best_d = -1, float("inf")
        for i, c in enumerate(centroids):
            d = _cos_dist(ob, c) * (1.0 + _independent_wobble(ob, c, jitter))
            if d < best_d:
                best_i, best_d = i, d
        if best_i >= 0 and best_d < tau:
            assignments.append(best_i)
            c = centroids[best_i]
            centroids[best_i] = [(1 - alpha) * cv + alpha * ov for cv, ov in zip(c, ob)]
        else:
            centroids.append(list(ob))
            assignments.append(len(centroids) - 1)
    return assignments, len(centroids)


def pairwise_agreement(a: list[int], b: list[int]) -> float:
    """Rand index: over all observation pairs, do A and B agree on
    same-track-or-not? Track IDs are arbitrary, so compare the *partition*."""
    n = len(a)
    agree = total = 0
    for i in range(n):
        for j in range(i + 1, n):
            total += 1
            if (a[i] == a[j]) == (b[i] == b[j]):
                agree += 1
    return agree / total if total else 1.0


def decision_margins(observations, tau: float, alpha: float = 0.3):
    """The distribution of how *close* each decision was.

    Two near-misses can flip under a perturbation: the argmin margin
    (d2 - d1, which track wins) and the threshold gap (|d1 - tau|, assign
    versus start a new track). A perturbation far below both cannot change
    anything, which is the whole point — substitutability is not about the
    size of the implementation difference, it is about that size *relative to
    the decision margins*.
    """
    centroids: list[list[float]] = []
    argmin_margins, threshold_gaps = [], []
    for ob in observations:
        ds = sorted(_cos_dist(ob, c) for c in centroids)
        if ds:
            threshold_gaps.append(abs(ds[0] - tau))
            if len(ds) > 1:
                argmin_margins.append(ds[1] - ds[0])
        if ds and ds[0] < tau:
            i = min(range(len(centroids)), key=lambda k: _cos_dist(ob, centroids[k]))
            centroids[i] = [(1 - alpha) * cv + alpha * ov for cv, ov in zip(centroids[i], ob)]
        else:
            centroids.append(list(ob))
    return argmin_margins, threshold_gaps


def divergence_profile(a: list[int], b: list[int]) -> tuple[int, bool, int]:
    """(first frame where the induced partition diverges, ever re-converged?,
    number of frames assigned differently after first divergence).

    Re-convergence is tested on the *prefix partition*: at frame k, does each
    tracker group frame k with the same earlier frames?
    """
    def prefix_group(assign, k):
        return frozenset(i for i in range(k) if assign[i] == assign[k])

    first = -1
    diverged_after = 0
    reconverged = False
    for k in range(len(a)):
        same = prefix_group(a, k) == prefix_group(b, k)
        if not same and first < 0:
            first = k
        if first >= 0:
            if not same:
                diverged_after += 1
            elif k > first:
                reconverged = True
    return first, reconverged, diverged_after
