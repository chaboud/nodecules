"""Timelines — where a timestamp comes from, how fine it is, and how two
clocks relate.

Founder input, 2026-09-06: *the domain and provenance of time are
themselves metadata; there will be clock skew across devices and media
types; and milliseconds are not enough for some content and actions —
Vegas used 100 ns "nanos" as its positioning primitive.* This module is
the substrate's answer, additive to `core/time.py`: `TimeRange` in
meeting-relative milliseconds keeps working unchanged, and is exactly a
`Span` on a timeline whose timebase is 1/1000.

Three ideas, all data:

- **Timebase** — seconds per tick, as an exact rational. Milliseconds are
  1/1000; Vegas nanos are 1/10,000,000; a 48 kHz sample clock is 1/48000;
  29.97 fps video is 1001/30000. A position is an integer count of ticks,
  so sample positions and frame positions are exact in their own
  timeline. Rounding happens only when converting, and is reported.
- **Timeline** — a named clock domain: its timebase, what tick zero means
  (meeting start, device boot, the Unix epoch, the start of a media
  file), what kind of clock drives it, and which device or stream owns
  it. This is the *domain and provenance* of every instant on it. A
  timeline is node-shaped (`as_node_data`) so it lives in the store like
  everything else.
- **TimelineMap** — how one timeline relates to another: measured anchor
  pairs (this tick here corresponded to that tick there), a nominal rate
  from the two timebases, and the map's own provenance (measured,
  declared, assumed) and error bound. Skew and drift are what the anchors
  express. Conversion between two clocks is therefore never implicit:
  it goes through a map that can be named, hashed, and reconsidered.

Nothing here reads a real clock. `TimeSource` in `core/time.py` still
owns that, injected.
"""

from __future__ import annotations

from fractions import Fraction
from math import floor, gcd
from typing import Literal, Mapping, Optional, Tuple

from pydantic import BaseModel, ConfigDict, Field, model_validator

from .time import TimeRange

Rounding = Literal["nearest", "floor", "ceil"]
ClockKind = Literal["media", "sample-counter", "monotonic", "wall", "derived"]
Provenance = Literal["measured", "declared", "assumed"]


class Timebase(BaseModel):
    """Seconds per tick as `num/den`, kept in lowest terms."""

    model_config = ConfigDict(frozen=True)

    num: int = Field(gt=0)
    den: int = Field(gt=0)

    @model_validator(mode="after")
    def _reduce(self) -> "Timebase":
        g = gcd(self.num, self.den)
        if g != 1:
            object.__setattr__(self, "num", self.num // g)
            object.__setattr__(self, "den", self.den // g)
        return self

    def seconds_per_tick(self) -> Fraction:
        return Fraction(self.num, self.den)

    def ticks_per_second(self) -> Fraction:
        return Fraction(self.den, self.num)


MILLISECONDS = Timebase(num=1, den=1_000)
NANOS = Timebase(num=1, den=10_000_000)  # 100 ns — Vegas's positioning tick
NANOSECONDS = Timebase(num=1, den=1_000_000_000)


def sample_clock(rate_hz: int) -> Timebase:
    """The timebase of a sample counter at `rate_hz` (48000 → 1/48000)."""
    return Timebase(num=1, den=rate_hz)


def frame_clock(fps_num: int, fps_den: int = 1) -> Timebase:
    """The timebase of a frame counter at `fps_num/fps_den` frames per second
    (30000/1001 for 29.97 → 1001/30000 seconds per frame)."""
    return Timebase(num=fps_den, den=fps_num)


class Timeline(BaseModel):
    """A clock domain. Every instant names one; two instants on different
    timelines cannot be compared without a `TimelineMap`."""

    model_config = ConfigDict(frozen=True)

    id: str = Field(min_length=1)
    timebase: Timebase
    origin: str = Field(min_length=1)  # what tick 0 means: "meeting-start", "unix-epoch", "device-boot", "media-start"
    clock: ClockKind = "derived"
    source: str = ""  # the device, file, or stream that owns the clock
    note: str = ""

    def as_node_data(self) -> dict:
        return self.model_dump(mode="json")


class Instant(BaseModel):
    """A position on one timeline: an integer tick count."""

    model_config = ConfigDict(frozen=True)

    ticks: int
    timeline: str

    def seconds(self, timelines: Mapping[str, Timeline]) -> Fraction:
        return self.ticks * timelines[self.timeline].timebase.seconds_per_tick()


class Span(BaseModel):
    """A closed-open interval `[start, end)` of ticks on one timeline — the
    generalisation of `TimeRange`, which is a Span on a 1/1000 timeline."""

    model_config = ConfigDict(frozen=True)

    start: int
    end: int
    timeline: str

    @model_validator(mode="after")
    def _bounds(self) -> "Span":
        if self.end <= self.start:
            raise ValueError(f"Span end ({self.end}) must be > start ({self.start})")
        return self

    def duration(self) -> int:
        return self.end - self.start

    def contains(self, ticks: int) -> bool:
        return self.start <= ticks < self.end

    def _same(self, other: "Span") -> None:
        if other.timeline != self.timeline:
            raise ValueError(
                f"spans on different timelines ({self.timeline!r} vs {other.timeline!r}); convert first"
            )

    def intersects(self, other: "Span") -> bool:
        self._same(other)
        return self.start < other.end and other.start < self.end

    def intersection(self, other: "Span") -> Optional["Span"]:
        self._same(other)
        lo, hi = max(self.start, other.start), min(self.end, other.end)
        return None if lo >= hi else Span(start=lo, end=hi, timeline=self.timeline)

    def union(self, other: "Span") -> "Span":
        self._same(other)
        return Span(start=min(self.start, other.start), end=max(self.end, other.end), timeline=self.timeline)

    def shift(self, delta: int) -> "Span":
        return Span(start=self.start + delta, end=self.end + delta, timeline=self.timeline)

    @classmethod
    def from_time_range(cls, tr: TimeRange, timeline: str) -> "Span":
        return cls(start=tr.start_ms, end=tr.end_ms, timeline=timeline)

    def to_time_range(self, timelines: Mapping[str, Timeline]) -> TimeRange:
        """Only for a timeline whose timebase is milliseconds — the bridge to
        the existing meeting-relative types."""
        tb = timelines[self.timeline].timebase
        if tb != MILLISECONDS:
            raise ValueError(f"timeline {self.timeline!r} is not in milliseconds ({tb.num}/{tb.den})")
        return TimeRange(start_ms=self.start, end_ms=self.end)


class Anchor(BaseModel):
    """One measured correspondence: tick `src` on the source timeline was
    observed at the same moment as tick `dst` on the destination."""

    model_config = ConfigDict(frozen=True)

    src: int
    dst: int


class TimelineMap(BaseModel):
    """How to take an instant from `src` to `dst`. Between anchors the map
    is linear through the measured pairs (that is where skew and drift
    live); outside them it extrapolates at the nominal rate implied by the
    two timebases from the nearest anchor. With one anchor the map is a
    pure offset at nominal rate."""

    model_config = ConfigDict(frozen=True)

    src: str
    dst: str
    anchors: Tuple[Anchor, ...] = Field(min_length=1)
    provenance: Provenance = "assumed"
    error_ticks: int = Field(default=0, ge=0)  # ± bound, in dst ticks
    note: str = ""

    @model_validator(mode="after")
    def _sorted_anchors(self) -> "TimelineMap":
        srcs = [a.src for a in self.anchors]
        if srcs != sorted(srcs) or len(set(srcs)) != len(srcs):
            raise ValueError("anchors must be strictly increasing in src")
        if self.src == self.dst:
            raise ValueError("a map from a timeline to itself is an identity; do not declare one")
        return self

    def measured_rate(self) -> Optional[Fraction]:
        """dst ticks per src tick across the outermost anchors, if two or more."""
        if len(self.anchors) < 2:
            return None
        a, b = self.anchors[0], self.anchors[-1]
        return Fraction(b.dst - a.dst, b.src - a.src)


class Converted(BaseModel):
    """An instant on the destination timeline plus what the conversion
    admits about itself: whether it was exact (no rounding) and the bound
    and provenance inherited from the map."""

    model_config = ConfigDict(frozen=True)

    instant: Instant
    exact: bool
    error_ticks: int
    provenance: Provenance


def _round(x: Fraction, rounding: Rounding) -> int:
    if rounding == "floor":
        return floor(x)
    if rounding == "ceil":
        return -floor(-x)
    return floor(x + Fraction(1, 2))


def convert(
    instant: Instant,
    via: TimelineMap,
    timelines: Mapping[str, Timeline],
    *,
    rounding: Rounding = "nearest",
) -> Converted:
    """Move an instant across a map. Exact rational arithmetic throughout;
    the only rounding is the final step to an integer tick, and `exact`
    says whether it was needed."""
    if instant.timeline != via.src:
        raise ValueError(f"instant is on {instant.timeline!r}; map starts at {via.src!r}")
    src_tb = timelines[via.src].timebase
    dst_tb = timelines[via.dst].timebase
    nominal = src_tb.seconds_per_tick() / dst_tb.seconds_per_tick()  # dst ticks per src tick
    anchors = via.anchors
    t = instant.ticks
    if t <= anchors[0].src:
        a = anchors[0]
        value = a.dst + (t - a.src) * nominal
    elif t >= anchors[-1].src:
        a = anchors[-1]
        value = a.dst + (t - a.src) * nominal
    else:
        lo = max(i for i, a in enumerate(anchors) if a.src <= t)
        a, b = anchors[lo], anchors[lo + 1]
        rate = Fraction(b.dst - a.dst, b.src - a.src)
        value = a.dst + (t - a.src) * rate
    ticks = _round(value, rounding)
    return Converted(
        instant=Instant(ticks=ticks, timeline=via.dst),
        exact=(value == ticks),
        error_ticks=via.error_ticks,
        provenance=via.provenance,
    )


def convert_span(
    span: Span,
    via: TimelineMap,
    timelines: Mapping[str, Timeline],
) -> Tuple[Span, bool]:
    """Start rounds down and end rounds up, so the converted span covers
    the original. Returns the span and whether both ends were exact."""
    s = convert(Instant(ticks=span.start, timeline=span.timeline), via, timelines, rounding="floor")
    e = convert(Instant(ticks=span.end, timeline=span.timeline), via, timelines, rounding="ceil")
    return Span(start=s.instant.ticks, end=e.instant.ticks, timeline=via.dst), s.exact and e.exact


def inverse(via: TimelineMap) -> TimelineMap:
    """The same correspondence read the other way."""
    return TimelineMap(
        src=via.dst,
        dst=via.src,
        anchors=tuple(sorted((Anchor(src=a.dst, dst=a.src) for a in via.anchors), key=lambda a: a.src)),
        provenance=via.provenance,
        error_ticks=via.error_ticks,
        note=via.note,
    )


__all__ = [
    "MILLISECONDS",
    "NANOS",
    "NANOSECONDS",
    "Anchor",
    "ClockKind",
    "Converted",
    "Instant",
    "Provenance",
    "Rounding",
    "Span",
    "Timebase",
    "Timeline",
    "TimelineMap",
    "convert",
    "convert_span",
    "frame_clock",
    "inverse",
    "sample_clock",
]
