"""Typed strip-access ADT (PR-r1).

Declares *how* a node reads a strip, as data — so the scheduler can
derive ordering, the cycle validator can reason statically, and cache
keys can be computed without running the node.

The ADT is deliberately small. It was derived by enumerating the access
shapes of every node in the stenota graph (see `REFERENCE-MODEL.md` and
`test_strip_access.py`); it carries exactly those shapes and nothing
speculative:

- `AllPattern`    — whole-strip read.
- `LatestPattern` — single most-recent element (a one-element strip such
                    as `audio.wav` reads as `Latest`).
- `RangePattern`  — windowed read; names which temporal field of the
                    target events the window tests against.

Ordinal indexing, `Before`/`After`, and IIR self-reference patterns are
intentionally absent — no current node uses them. They are added when a
real streaming node needs them, not before.

Everything here is a Pydantic v2 model so access declarations round-trip
through graph JSON. Discriminated unions (`TimeExpr`, `AccessPattern`)
use a `kind` field as the discriminator.
"""

from __future__ import annotations

from typing import Annotated, Literal, Union

from pydantic import BaseModel, ConfigDict, Field


# --- TimeExpr -------------------------------------------------------------
#
# Symbolic time expressions, resolved at generation time against the
# node's active window. Only the forms a real windowed node needs:
# the active window's bounds, and absolute meeting-relative ms.


class SelfWindowStart(BaseModel):
    """The start (inclusive) of the node's active window."""

    model_config = ConfigDict(frozen=True)
    kind: Literal["self_window_start"] = "self_window_start"


class SelfWindowEnd(BaseModel):
    """The end (exclusive) of the node's active window."""

    model_config = ConfigDict(frozen=True)
    kind: Literal["self_window_end"] = "self_window_end"


class AbsoluteMs(BaseModel):
    """A fixed meeting-relative timestamp, integer milliseconds."""

    model_config = ConfigDict(frozen=True)
    kind: Literal["absolute_ms"] = "absolute_ms"
    ms: int = Field(ge=0)


TimeExpr = Annotated[
    Union[SelfWindowStart, SelfWindowEnd, AbsoluteMs],
    Field(discriminator="kind"),
]


# --- AccessPattern --------------------------------------------------------
#
# How a node reads a strip. Exactly the three shapes the stenota graph
# exhibits today.


class AllPattern(BaseModel):
    """Whole-strip read — every element.

    Used by nodes that consume a strip in bulk and do their own
    filtering / joining internally (turns, participation, speaker
    re-ID). A node declaring `All` depends on the entire strip: any
    change to it dirties the node.
    """

    model_config = ConfigDict(frozen=True)
    kind: Literal["all"] = "all"


class LatestPattern(BaseModel):
    """Single most-recent element.

    A one-element strip (a binary blob such as `audio.wav`) reads as
    `Latest`. So does "the current value" of any strip when only the
    head matters.
    """

    model_config = ConfigDict(frozen=True)
    kind: Literal["latest"] = "latest"


class RangePattern(BaseModel):
    """Windowed read — elements whose temporal field intersects a window.

    `field` names *which* temporal attribute of the target elements the
    window tests against. Strip elements can carry more than one time
    field (a claim has both `source_window` and `time_ranges`); the
    window read must say which it means. A list-valued field intersects
    the window iff any of its ranges does.

    `start` / `end` are symbolic `TimeExpr`s, resolved against the
    reading node's active window at generation time.
    """

    model_config = ConfigDict(frozen=True)
    kind: Literal["range"] = "range"
    field: str = Field(min_length=1)
    start: TimeExpr
    end: TimeExpr


AccessPattern = Annotated[
    Union[AllPattern, LatestPattern, RangePattern],
    Field(discriminator="kind"),
]


# --- StripAccess ----------------------------------------------------------


class StripAccess(BaseModel):
    """One declared read: a named strip plus the pattern it's read with.

    A node declares a list of these (`NodeSpec.reads_strip_patterns`).
    The scheduler derives ordering from them, the cycle validator checks
    them, and cache keys incorporate them — all without running the node.
    """

    model_config = ConfigDict(frozen=True)
    strip_name: str = Field(min_length=1)
    pattern: AccessPattern


__all__ = [
    "AbsoluteMs",
    "AccessPattern",
    "AllPattern",
    "LatestPattern",
    "RangePattern",
    "SelfWindowEnd",
    "SelfWindowStart",
    "StripAccess",
    "TimeExpr",
]
