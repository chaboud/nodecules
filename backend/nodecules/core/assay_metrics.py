"""Assay metrics — job-shaped deviation, registered by name (PR-d1).

A description names its metric (`Tolerance.metric`, see `descriptions.py`)
and the assay looks it up here. Every metric is an *error*: 0.0 means
identical, larger is worse, and the description's `max_value` is the
acceptance line. The registry is open (`register_metric`) so a new job
family brings its own scoring without touching this module.

Why the metric ships with the description rather than living in the
matcher: deviation is job-shaped. The colour toy in
`spikes/matching-bench/` scored plans by elementwise max-abs and that
looked like a law until the first two real contracts arrived — ASR is
scored by word error rate, diarization by a permutation-invariant DER
(cluster labels are arbitrary; `SPEAKER_00` in one run is `SPEAKER_03` in
another). A matcher with a built-in metric would have judged both wrong.

Three ship, grounded on the first three real descriptions:

  max_abs — elementwise max absolute difference over numeric sequences.
  wer     — word error rate: word-level Levenshtein distance divided by
            reference word count.
  der     — diarization error rate over exclusive speaker turns:
            (missed + false alarm + confusion) / reference speech time,
            minimised over injective label mappings.
"""

from __future__ import annotations

import itertools
from typing import Callable, Dict, Sequence, Tuple

Metric = Callable[[object, object], float]

_METRICS: Dict[str, Metric] = {}


def register_metric(name: str, fn: Metric) -> None:
    """Register a metric by name. Re-registering a name replaces it."""
    if not name:
        raise ValueError("metric name must be non-empty")
    _METRICS[name] = fn


def get_metric(name: str) -> Metric:
    try:
        return _METRICS[name]
    except KeyError:
        raise KeyError(
            f"unknown assay metric {name!r}; registered: {sorted(_METRICS)}"
        ) from None


def score(name: str, candidate: object, reference: object) -> float:
    """Apply the named metric. Errors are non-negative; 0.0 is identical."""
    value = float(get_metric(name)(candidate, reference))
    if value < 0.0:
        raise ValueError(f"metric {name!r} returned a negative error: {value}")
    return value


# --- max_abs ----------------------------------------------------------------


def max_abs(candidate: Sequence, reference: Sequence) -> float:
    """Elementwise max absolute difference.

    Elements may be numbers or equal-length tuples of numbers. Sequences
    must have equal length — a length mismatch is an interface failure,
    not a large deviation.
    """
    if len(candidate) != len(reference):
        raise ValueError(
            f"max_abs: length mismatch ({len(candidate)} vs {len(reference)})"
        )
    worst = 0.0
    for c, r in zip(candidate, reference):
        if isinstance(c, (int, float)) and isinstance(r, (int, float)):
            d = abs(float(c) - float(r))
        else:
            if len(c) != len(r):
                raise ValueError("max_abs: element arity mismatch")
            d = max((abs(float(a) - float(b)) for a, b in zip(c, r)), default=0.0)
        if d > worst:
            worst = d
    return worst


# --- wer --------------------------------------------------------------------


def _levenshtein(a: Sequence[str], b: Sequence[str]) -> int:
    if not a:
        return len(b)
    if not b:
        return len(a)
    prev = list(range(len(b) + 1))
    for i, x in enumerate(a, 1):
        cur = [i]
        for j, y in enumerate(b, 1):
            cur.append(min(prev[j] + 1, cur[j - 1] + 1, prev[j - 1] + (x != y)))
        prev = cur
    return prev[-1]


def wer(candidate_words: Sequence[str], reference_words: Sequence[str]) -> float:
    """Word error rate: edit distance over words / reference word count.

    An empty reference against a non-empty candidate is infinite error
    (every word is an insertion against nothing); empty against empty is
    0.0. Callers normalise case and punctuation before scoring — this
    function compares tokens exactly, on purpose.
    """
    if not reference_words:
        return 0.0 if not candidate_words else float("inf")
    return _levenshtein(candidate_words, reference_words) / len(reference_words)


# --- der --------------------------------------------------------------------

Turn = Tuple[int, int, str]  # (start_ms, end_ms, label), closed-open

_MAX_DER_LABELS = 8


def _label_at(turns: Sequence[Turn], start: int, end: int):
    for s, e, label in turns:
        if s <= start and end <= e:
            return label
    return None


def der(candidate: Sequence[Turn], reference: Sequence[Turn]) -> float:
    """Diarization error rate, permutation-invariant over labels.

    Turns are exclusive (at most one speaker at a time on each side —
    stenota's diar contract). The candidate's labels are mapped onto the
    reference's by the injective mapping that maximises matched speech;
    what remains is confusion. Brute force over mappings, capped at 8
    labels per side — beyond that, bring the Hungarian method.
    """
    ref_total = sum(e - s for s, e, _ in reference)
    if ref_total <= 0:
        return 0.0 if not candidate else float("inf")

    bounds = sorted({b for s, e, _ in (*candidate, *reference) for b in (s, e)})
    cells = []
    for s, e in zip(bounds, bounds[1:]):
        if e <= s:
            continue
        cells.append((_label_at(reference, s, e), _label_at(candidate, s, e), e - s))

    missed = sum(d for rl, hl, d in cells if rl is not None and hl is None)
    false_alarm = sum(d for rl, hl, d in cells if rl is None and hl is not None)
    both = sum(d for rl, hl, d in cells if rl is not None and hl is not None)

    overlap: Dict[Tuple[str, str], int] = {}
    for rl, hl, d in cells:
        if rl is not None and hl is not None:
            overlap[(hl, rl)] = overlap.get((hl, rl), 0) + d

    hyp_labels = sorted({hl for _, hl, _ in cells if hl is not None})
    ref_labels = sorted({rl for rl, _, _ in cells if rl is not None})
    if len(hyp_labels) > _MAX_DER_LABELS or len(ref_labels) > _MAX_DER_LABELS:
        raise ValueError(
            f"der: brute-force mapping supports up to {_MAX_DER_LABELS} labels per side"
        )

    best = 0
    if len(hyp_labels) <= len(ref_labels):
        for perm in itertools.permutations(ref_labels, len(hyp_labels)):
            matched = sum(overlap.get((h, r), 0) for h, r in zip(hyp_labels, perm))
            best = max(best, matched)
    else:
        for perm in itertools.permutations(hyp_labels, len(ref_labels)):
            matched = sum(overlap.get((h, r), 0) for h, r in zip(perm, ref_labels))
            best = max(best, matched)

    confusion = both - best
    return (missed + false_alarm + confusion) / ref_total


register_metric("max_abs", max_abs)
register_metric("wer", wer)
register_metric("der", der)


__all__ = [
    "Metric",
    "Turn",
    "der",
    "get_metric",
    "max_abs",
    "register_metric",
    "score",
    "wer",
]
