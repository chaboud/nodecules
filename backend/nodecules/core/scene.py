"""Scenes — presentation time, expiry, and the frame as a pure function.

Founder, 2026-09-13: *coordinated UI isn't "as fast as possible"; it's "at
the same time."* Scene graphs carry presentation time (in the future) and
expiration (for resiliency). This module is that contract, with no pixels:

- **A table has a timeline** (`core/timeline.py`). An element's validity
  is a `Span` on it — `present_at` inclusive, `expires_at` exclusive —
  never on a wall clock. Persistence is renewal: a long-lived element is
  republished with a later expiry.
- **The frame is a pure function of (manifest, instant).** `frame()` reads
  a manifest, keeps the elements whose validity contains `t`, orders them
  deterministically, and hashes the result. Same head, same instant, same
  frame — so a frame can be a produced node, and "what did this person see
  when they decided" is answerable from the store.
- **A surface has a clock, a refresh grid, and latencies**, and a map from
  the table's timeline to its own. Its **simultaneity bound** is knowable:
  the map's error, plus one refresh period, plus delivery and render
  latency, in table ticks. "At the same time" means within the worst
  bound among the surfaces, and a surface beyond the table's tolerance is
  *degraded*, visibly, rather than late silently.
- **Producers publish ahead.** `lead()` is the worst surface's bound; an
  element whose `present_at` is closer than that is late for someone.
- **Lateness is a policy**: `skip`, `present-late`, or `slip` to the next
  slot of the table's frame clock. `schedule()` applies it per surface and
  says, per element, the local tick it will be shown at and why.
- **Presented-at comes back** as table ticks (`presented()`), so sync
  quality is an observation like any other cost.
- **The frame carries data and choice structure, never pixels** (founder,
  2026-09-13). A payload is semantic; a presenter interprets it for its
  surface, and a user's or system's own interpreter — a screen reader is
  the classic case — is a legitimate presenter. Producers publish several
  ticks ahead and may be *instructive* about transitions (`enter`,
  `exit`: a kind and a duration) without owning them: a renderer keeps
  agency and may honour, shorten, or ignore a hint. `schedule` reports
  when a transition would begin and whether it had to be truncated.

No clocks are read here. A surface's `now` is passed in.
"""

from __future__ import annotations

from fractions import Fraction
from math import ceil
from typing import Any, Dict, Iterator, List, Literal, Mapping, Optional, Tuple

from pydantic import BaseModel, ConfigDict, Field, model_validator

from .store import Manifest, Node, Store, canonical_hash
from .timeline import Instant, Span, Timebase, Timeline, TimelineMap, convert, inverse

FRAME_KIND = "frame"
Lateness = Literal["skip", "present-late", "slip"]
Status = Literal["on-time", "late", "skipped", "slipped"]


# --- elements -----------------------------------------------------------------


class Transition(BaseModel):
    """A hint about how an element arrives or leaves: a kind the presenter
    may interpret ("fade", "slide", "cut", "renderer-choice") and how long
    it should take, in table ticks. Advisory: renderers keep agency."""

    model_config = ConfigDict(frozen=True)

    kind: str = "renderer-choice"
    duration: int = Field(default=0, ge=0)


class Element(BaseModel):
    """The data shape of a scene node: when it is meant to be fully shown,
    when it stops being true, where it goes, what the presenter interprets,
    and how it might arrive and leave."""

    model_config = ConfigDict(frozen=True)

    present_at: int
    expires_at: int
    region: str = ""
    z: int = 0
    payload: Any = None
    enter: Optional[Transition] = None
    exit: Optional[Transition] = None

    @model_validator(mode="after")
    def _ordered(self) -> "Element":
        if self.expires_at <= self.present_at:
            raise ValueError("an element must expire after it is presented; persistence is renewal")
        return self

    def validity(self, timeline: str) -> Span:
        return Span(start=self.present_at, end=self.expires_at, timeline=timeline)


def element_of(node: Node) -> Optional[Element]:
    """The element a node carries, if its data is element-shaped."""
    if not isinstance(node.data, Mapping) or "present_at" not in node.data or "expires_at" not in node.data:
        return None
    return Element(**node.data)


def renewed(node: Node, expires_at: int) -> Node:
    """The same element with a later expiry — a new node under the same
    name, which is how a long-lived element stays alive."""
    el = element_of(node)
    if el is None:
        raise ValueError(f"{node.id!r} is not element-shaped")
    if expires_at <= el.expires_at:
        raise ValueError("renewal must extend the expiry")
    return node.model_copy(update={"data": {**node.data, "expires_at": expires_at}})


# --- the frame ----------------------------------------------------------------


class FrameElement(BaseModel):
    model_config = ConfigDict(frozen=True)

    id: str
    content_hash: str
    region: str
    z: int
    present_at: int
    expires_at: int
    payload: Any = None
    enter: Optional[Transition] = None
    exit: Optional[Transition] = None


class Frame(BaseModel):
    """What the table shows at instant `t`, as of one manifest. Pure and
    content-addressed: `content_hash()` covers the manifest, the instant,
    and the ordered elements."""

    model_config = ConfigDict(frozen=True)

    scope: str
    timeline: str
    manifest: str
    t: int
    elements: Tuple[FrameElement, ...]

    def content_hash(self) -> str:
        return canonical_hash(self.model_dump(mode="json"))

    def as_node(self) -> Node:
        return Node(
            id=f"frames/{self.timeline}@{self.t}",
            kind=FRAME_KIND,
            scope=self.scope,
            data=self.model_dump(mode="json"),
        )


def frame(store: Store, manifest: Manifest, t: int, *, timeline: str) -> Frame:
    """The frame at `t`: every resident element whose validity contains `t`,
    ordered by (z, region, id). Elements whose data is pruned are not in
    the frame — a frame is what can be shown, not what once could."""
    chosen: List[FrameElement] = []
    for name, h in manifest.entries():
        node = store.get_by_hash(h)
        if node is None:
            continue
        el = element_of(node)
        if el is None or not el.validity(timeline).contains(t):
            continue
        chosen.append(
            FrameElement(
                id=name,
                content_hash=h,
                region=el.region,
                z=el.z,
                present_at=el.present_at,
                expires_at=el.expires_at,
                payload=el.payload,
                enter=el.enter,
                exit=el.exit,
            )
        )
    chosen.sort(key=lambda e: (e.z, e.region, e.id))
    return Frame(scope=manifest.scope, timeline=timeline, manifest=manifest.content_hash(), t=t, elements=tuple(chosen))


def expired(store: Store, manifest: Manifest, t: int) -> Tuple[str, ...]:
    """Names whose elements have expired by `t` — retention's input."""
    out: List[str] = []
    for name, h in manifest.entries():
        node = store.get_by_hash(h)
        el = element_of(node) if node is not None else None
        if el is not None and el.expires_at <= t:
            out.append(name)
    return tuple(sorted(out))


# --- surfaces and simultaneity ------------------------------------------------------


class Surface(BaseModel):
    """Something that presents: a clock (its timeline), a refresh grid, its
    latencies, and the map from the table's timeline to its clock."""

    model_config = ConfigDict(frozen=True)

    id: str
    timeline: str  # the surface's own clock
    refresh: Timebase  # seconds per refresh: 1/60, 1/120, 1/10
    to_local: TimelineMap  # src = table timeline, dst = this surface's timeline
    delivery_ticks: int = Field(default=0, ge=0)  # head → surface, in table ticks
    render_ticks: int = Field(default=0, ge=0)  # decode/layout/draw, in table ticks
    modalities: Tuple[str, ...] = ()


def _ticks(seconds: Fraction, tb: Timebase, rounding: str = "ceil") -> int:
    x = seconds / tb.seconds_per_tick()
    return ceil(x) if rounding == "ceil" else int(x)


def simultaneity_bound(surface: Surface, timelines: Mapping[str, Timeline]) -> int:
    """How far from the intended instant this surface may present, in table
    ticks: map error + one refresh period + delivery + render."""
    table_tb = timelines[surface.to_local.src].timebase
    local_tb = timelines[surface.timeline].timebase
    err_seconds = surface.to_local.error_ticks * local_tb.seconds_per_tick()
    err = _ticks(err_seconds, table_tb)
    refresh = _ticks(surface.refresh.seconds_per_tick(), table_tb)
    return err + refresh + surface.delivery_ticks + surface.render_ticks


class TablePolicy(BaseModel):
    """The table's presentation contract."""

    model_config = ConfigDict(frozen=True)

    timeline: str
    frame_clock: Timebase = Timebase(num=1, den=60)  # the slot grid for `slip`
    tolerance_ticks: int  # the simultaneity the table promises, in table ticks
    lateness: Lateness = "skip"


def lead(surfaces: Tuple[Surface, ...], timelines: Mapping[str, Timeline]) -> int:
    """How far ahead a producer must publish so every surface can be on
    time: the worst simultaneity bound."""
    return max((simultaneity_bound(s, timelines) for s in surfaces), default=0)


def degraded(surfaces: Tuple[Surface, ...], policy: TablePolicy, timelines: Mapping[str, Timeline]) -> Tuple[str, ...]:
    """Surfaces that cannot promise the table's simultaneity — named, not
    silently late."""
    return tuple(s.id for s in surfaces if simultaneity_bound(s, timelines) > policy.tolerance_ticks)


# --- scheduling on a surface -------------------------------------------------------------


class ScheduledItem(BaseModel):
    model_config = ConfigDict(frozen=True)

    id: str
    status: Status
    local_tick: Optional[int]  # when this surface will have it fully shown, on its own clock; None if skipped
    begin_local: Optional[int] = None  # when the enter transition would start; equals local_tick when there is none
    truncated: bool = False  # the transition could not run in full and the renderer must shorten it


class Schedule(BaseModel):
    model_config = ConfigDict(frozen=True)

    surface: str
    frame: str  # the frame's content hash
    now_local: int
    items: Tuple[ScheduledItem, ...]


def _snap_up(tick: Fraction | int, period: Fraction) -> int:
    """The first grid boundary at or after `tick`."""
    return int(ceil(Fraction(tick) / period) * period)


def _snap_down(tick: Fraction | int, period: Fraction) -> int:
    """The last grid boundary at or before `tick`."""
    return int((Fraction(tick) // period) * period)


def schedule(surface: Surface, fr: Frame, now_local: int, policy: TablePolicy, timelines: Mapping[str, Timeline]) -> Schedule:
    """Decide, per element, when this surface shows it. `present_at` is
    converted to the surface's clock and snapped up to its refresh grid; if
    that is already past `now + render`, the table's lateness policy
    applies."""
    local_tb = timelines[surface.timeline].timebase
    table_tb = timelines[policy.timeline].timebase
    refresh_local = surface.refresh.seconds_per_tick() / local_tb.seconds_per_tick()
    slot_local = policy.frame_clock.seconds_per_tick() / local_tb.seconds_per_tick()
    render_local = _ticks(surface.render_ticks * table_tb.seconds_per_tick(), local_tb)
    earliest = now_local + render_local
    items: List[ScheduledItem] = []
    for el in fr.elements:
        target = convert(Instant(ticks=el.present_at, timeline=policy.timeline), surface.to_local, timelines).instant.ticks
        at = _snap_up(target, refresh_local)
        enter_local = _ticks((el.enter.duration if el.enter else 0) * table_tb.seconds_per_tick(), local_tb, "floor")
        begin = _snap_down(at - enter_local, refresh_local) if enter_local else at
        if at >= earliest:
            truncated = begin < earliest
            items.append(ScheduledItem(id=el.id, status="on-time", local_tick=at, begin_local=max(begin, _snap_up(earliest, refresh_local)) if truncated else begin, truncated=truncated))
            continue
        if policy.lateness == "skip":
            items.append(ScheduledItem(id=el.id, status="skipped", local_tick=None))
        elif policy.lateness == "present-late":
            late_at = _snap_up(earliest, refresh_local)
            items.append(ScheduledItem(id=el.id, status="late", local_tick=late_at, begin_local=late_at, truncated=enter_local > 0))
        else:
            slip_at = _snap_up(earliest, slot_local)
            items.append(ScheduledItem(id=el.id, status="slipped", local_tick=slip_at, begin_local=slip_at, truncated=enter_local > 0))
    return Schedule(surface=surface.id, frame=fr.content_hash(), now_local=now_local, items=tuple(items))


def presented(surface: Surface, sched: Schedule, timelines: Mapping[str, Timeline]) -> Dict[str, int]:
    """When each shown element was presented, back on the table's timeline —
    the observation a surface reports."""
    back = inverse(surface.to_local)
    out: Dict[str, int] = {}
    for item in sched.items:
        if item.local_tick is None:
            continue
        out[item.id] = convert(Instant(ticks=item.local_tick, timeline=surface.timeline), back, timelines).instant.ticks
    return out


def spread(reports: Mapping[str, Mapping[str, int]]) -> Dict[str, int]:
    """Per element, the widest gap between surfaces' presented instants, in
    table ticks — the measured simultaneity."""
    by_el: Dict[str, List[int]] = {}
    for _surface, rep in reports.items():
        for el, tick in rep.items():
            by_el.setdefault(el, []).append(tick)
    return {el: max(ts) - min(ts) for el, ts in by_el.items() if len(ts) > 1}


__all__ = [
    "FRAME_KIND",
    "Element",
    "Frame",
    "FrameElement",
    "Lateness",
    "Schedule",
    "ScheduledItem",
    "Surface",
    "TablePolicy",
    "Transition",
    "degraded",
    "element_of",
    "expired",
    "frame",
    "lead",
    "presented",
    "renewed",
    "schedule",
    "simultaneity_bound",
    "spread",
]
