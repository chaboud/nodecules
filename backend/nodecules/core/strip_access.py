"""Typed strip-access ADT (PR-r1).

Declares *how* a node reads a strip, as data — so the scheduler can
derive ordering, the cycle validator can reason statically, and cache
keys can be computed without running the node.

The ADT is deliberately small. It was derived by enumerating the access
shapes of every node in the stenota graph (see `REFERENCE-MODEL.md` and
`test_strip_access.py`); it carries exactly those shapes and nothing
speculative:

- `AllPattern`     — whole-strip read.
- `LatestPattern`  — single most-recent element (a one-element strip such
                     as `audio.wav` reads as `Latest`).
- `RangePattern`   — windowed read; names which temporal field of the
                     target events the window tests against. Its bounds
                     may be relative: `Ago` looks back from the reader's
                     window or from the strip's head ("the last five
                     minutes").
- `OrdinalPattern` — elements by position, relative to the strip's head or
                     to the reader's own position ("the last twenty", "the
                     entry before mine"); `last(n)` and `previous(n)` build
                     the common ones.

The relative forms returned on 2026-10-10 (founder: "live observation like
keyhole never works without relative relationships"; REFERENCE-MODEL §5).
They are symbolic: what they resolve to at production time is recorded in
the receipt and in the cache key, never written back into the declaration.
They only look back: every offset is at most zero, so a forward reference
cannot be written. They ship with their floor: a read that reaches into
elements a strip has released comes back lost, naming what was released
(`generation.read`, `strip_nodes.strip_trim`).

IIR self-reference (a node reading its own previous output) is still
absent; it ships with settling nodes and their warm-up (§17).

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


class Ago(BaseModel):
    """A time looking back from an anchor: the reader's window start or end,
    or the strip's head (the latest time any element reaches in the field the
    range tests, so "the last five seconds" needs no clock). `ms` is never
    negative: a relative read only looks back."""

    model_config = ConfigDict(frozen=True)
    kind: Literal["ago"] = "ago"
    anchor: Literal["self_window_start", "self_window_end", "head"]
    ms: int = Field(default=0, ge=0)


TimeExpr = Annotated[
    Union[SelfWindowStart, SelfWindowEnd, AbsoluteMs, Ago],
    Field(discriminator="kind"),
]


# --- OrdinalExpr -----------------------------------------------------------
#
# Positions in a strip, for reads by count rather than by time. Positions are
# absolute: an element keeps its index after earlier ones are released.


class FromHead(BaseModel):
    """The strip's length plus `offset` (at most zero): `FromHead()` is the end,
    `FromHead(offset=-20)` is twenty elements back from it."""

    model_config = ConfigDict(frozen=True)
    kind: Literal["from_head"] = "from_head"
    offset: int = Field(default=0, le=0)


class FromSelf(BaseModel):
    """The reader's own position plus `offset` (at most zero). The position
    is a param of the reading node (`position`), like its window."""

    model_config = ConfigDict(frozen=True)
    kind: Literal["from_self"] = "from_self"
    offset: int = Field(default=0, le=0)


class Index(BaseModel):
    """An absolute position."""

    model_config = ConfigDict(frozen=True)
    kind: Literal["index"] = "index"
    index: int = Field(ge=0)


OrdinalExpr = Annotated[
    Union[FromHead, FromSelf, Index],
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


class OrdinalPattern(BaseModel):
    """Elements by position, `[start, end)` in the strip's order.

    A read that ends past the strip's head is waiting for elements that do
    not exist yet (the production is pending until they arrive). A read that
    starts before the strip's first element is clipped (nothing was ever
    there). A read that starts in elements the strip has released is lost,
    and says which."""

    model_config = ConfigDict(frozen=True)
    kind: Literal["ordinal"] = "ordinal"
    start: OrdinalExpr
    end: OrdinalExpr = Field(default_factory=FromHead)


def last(n: int) -> OrdinalPattern:
    """The last `n` elements."""
    if n < 1:
        raise ValueError("last(n) needs n >= 1")
    return OrdinalPattern(start=FromHead(offset=-n), end=FromHead())


def previous(n: int = 1) -> OrdinalPattern:
    """The `n` elements before the reader's own position (its `position` param)."""
    if n < 1:
        raise ValueError("previous(n) needs n >= 1")
    return OrdinalPattern(start=FromSelf(offset=-n), end=FromSelf())


AccessPattern = Annotated[
    Union[AllPattern, LatestPattern, RangePattern, OrdinalPattern],
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
    "Ago",
    "AllPattern",
    "FromHead",
    "FromSelf",
    "Index",
    "LatestPattern",
    "OrdinalExpr",
    "OrdinalPattern",
    "RangePattern",
    "SelfWindowEnd",
    "SelfWindowStart",
    "StripAccess",
    "TimeExpr",
    "last",
    "previous",
]
