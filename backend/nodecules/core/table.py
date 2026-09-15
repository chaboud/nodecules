"""The table — open collaboration at the edge, and attention.

Founder, 2026-09-15: *"open edge collaboration and attention is a fun
start. Think Figma. I don't have to follow someone, but I can."*

A table is a scope. Everyone at it — people and agents — places elements
freely on their own replica and converges (`core/replica.py`); nobody
waits. This module adds what a shared table needs beyond the store:

- **Participants** are nodes (`participants/<id>`): a person or an agent,
  with a display name.
- **Attention is an element** (`attention/<participant>`) in the
  `presence` region: what the participant is looking at or working on —
  a region, an element, a viewport — with an expiry. Presence is renewal:
  a participant that stops renewing fades out of everyone's frame on
  schedule, which is the resilience rule from `core/scene.py` applied to
  people. Attention is data like everything else, so "who was looking at
  what when the decision was made" is in the store.
- **Following is optional and per person.** `follow(me, them)` records it
  on my attention; `view_for(me, t)` resolves my *effective* focus — mine,
  or the focus of whom I follow, transitively with a cycle guard — and
  says whom it came from. A frame for a participant is the table's frame
  plus that focus; the presenter decides what to do with it (raise the
  focused region, scroll to it, read it first). Following never moves
  anyone else's attention.
- **Open collaboration on one element** converges field-wise: when two
  participants change the same element concurrently, each side's changed
  payload fields win, and a field both changed goes to the deterministic
  tie-break (the larger content hash), which is arbitrary but identical
  on every replica and visible in history. Registered as the merge rule
  for every `ui.*` kind — the Figma default of last-writer-per-property,
  without a clock.

Not here: decisions and presentations (the choice-architecture half),
the responsible adult, presenters beyond text, any transport.
"""

from __future__ import annotations

from typing import Any, Dict, List, Mapping, Optional, Set, Tuple

from pydantic import BaseModel, ConfigDict, Field

from .scene import Element, Frame, element_of, frame, renewed
from .store import Manifest, Node, Store, register_merge

PRESENCE_REGION = "presence"
PARTICIPANT_KIND = "participant"
ATTENTION_KIND = "ui.attention"
MAX_FOLLOW_DEPTH = 8


def participant_id(name: str) -> str:
    return f"participants/{name}"


def attention_id(name: str) -> str:
    return f"attention/{name}"


class Participant(BaseModel):
    model_config = ConfigDict(frozen=True)

    id: str
    kind: str = "human"  # or "agent"
    display: str = ""


class Focus(BaseModel):
    """What a participant is attending to. All parts optional; a presenter
    interprets what is there."""

    model_config = ConfigDict(frozen=True)

    region: Optional[str] = None
    element: Optional[str] = None
    viewport: Optional[Dict[str, Any]] = None  # e.g. {"x", "y", "w", "h"} in the table's own units


class View(BaseModel):
    """A participant's effective focus and where it came from."""

    model_config = ConfigDict(frozen=True)

    participant: str
    focus: Optional[Focus]
    following: Optional[str] = None  # whom I asked to follow, if anyone
    via: Optional[str] = None  # whose attention the focus actually came from (None: my own)
    frame: Frame


# --- participants ------------------------------------------------------------------


def join(store: Store, table: str, p: Participant, *, author: str = "") -> Manifest:
    tx = store.transaction(table, author=author or p.id)
    tx.put(Node(id=participant_id(p.id), kind=PARTICIPANT_KIND, scope=table, data=p.model_dump()))
    return tx.commit(note=f"{p.id} joins")


def participants(store: Store, manifest: Manifest) -> Tuple[Participant, ...]:
    out: List[Participant] = []
    for name, h in manifest.entries():
        if name.startswith("participants/"):
            node = store.get_by_hash(h)
            if node is not None and node.data is not None:
                out.append(Participant(**node.data))
    return tuple(sorted(out, key=lambda p: p.id))


# --- attention --------------------------------------------------------------------------


def attend(
    store: Store,
    table: str,
    me: str,
    focus: Optional[Focus],
    *,
    now: int,
    ttl: int,
    following: Optional[str] = None,
) -> Manifest:
    """Publish (or renew) my attention: present now, expiring at now + ttl.
    Keeps my `following` unless given."""
    cur = store.get(store.current(table), attention_id(me))
    prev = element_of(cur) if isinstance(cur, Node) else None
    if following is None and prev is not None:
        following = (prev.payload or {}).get("following")
    el = Element(
        present_at=now,
        expires_at=now + ttl,
        region=PRESENCE_REGION,
        payload={"participant": me, "focus": focus.model_dump() if focus else None, "following": following},
    )
    tx = store.transaction(table, author=me)
    tx.put(Node(id=attention_id(me), kind=ATTENTION_KIND, scope=table, data=el.model_dump()))
    return tx.commit(note=f"{me} attends")


def renew(store: Store, table: str, me: str, *, until: int) -> Manifest:
    """Keep my presence alive without changing what I attend to."""
    cur = store.get(store.current(table), attention_id(me))
    if not isinstance(cur, Node):
        raise ValueError(f"{me} has no attention to renew")
    tx = store.transaction(table, author=me)
    tx.put(renewed(cur, until))
    return tx.commit(note=f"{me} renews")


def follow(store: Store, table: str, me: str, them: Optional[str], *, now: int, ttl: int) -> Manifest:
    """Follow someone (or nobody, with None). My own focus is kept."""
    cur = store.get(store.current(table), attention_id(me))
    prev = element_of(cur) if isinstance(cur, Node) else None
    focus = Focus(**prev.payload["focus"]) if prev and prev.payload and prev.payload.get("focus") else None
    return attend(store, table, me, focus, now=now, ttl=ttl, following=them or "")


def attention_of(store: Store, manifest: Manifest, who: str, t: int) -> Optional[Element]:
    """Their attention element if it is alive at `t`."""
    node = store.get(manifest, attention_id(who))
    el = element_of(node) if isinstance(node, Node) else None
    if el is None or not el.validity("").contains(t):
        return None
    return el


def present(store: Store, manifest: Manifest, t: int) -> Tuple[str, ...]:
    """Who is present at `t`: everyone whose attention is alive."""
    out = []
    for name, h in manifest.entries():
        if name.startswith("attention/"):
            node = store.get_by_hash(h)
            el = element_of(node) if node is not None else None
            if el is not None and el.validity("").contains(t):
                out.append(name.split("/", 1)[1])
    return tuple(sorted(out))


def view_for(store: Store, manifest: Manifest, me: str, t: int, *, timeline: str) -> View:
    """My effective focus at `t`: my own, or the focus of whom I follow,
    transitively, stopping at a cycle, an absent participant, or the depth
    cap — in which case I fall back to my own focus and `via` says so."""
    fr = frame(store, manifest, t, timeline=timeline)
    mine = attention_of(store, manifest, me, t)
    my_focus = Focus(**mine.payload["focus"]) if mine and mine.payload and mine.payload.get("focus") else None
    following = (mine.payload or {}).get("following") if mine else None
    following = following or None
    if not following:
        return View(participant=me, focus=my_focus, following=None, via=None, frame=fr)
    seen: Set[str] = {me}
    who = following
    depth = 0
    while who and who not in seen and depth < MAX_FOLLOW_DEPTH:
        seen.add(who)
        theirs = attention_of(store, manifest, who, t)
        if theirs is None:
            break
        next_who = (theirs.payload or {}).get("following") or None
        if not next_who or next_who in seen:
            focus = Focus(**theirs.payload["focus"]) if theirs.payload.get("focus") else None
            return View(participant=me, focus=focus, following=following, via=who, frame=fr)
        who = next_who
        depth += 1
    return View(participant=me, focus=my_focus, following=following, via=None, frame=fr)


# --- open collaboration on one element ------------------------------------------------------


def fieldwise_merge(base: Optional[Node], lo: Node, hi: Node) -> Node:
    """Three-way, per payload field: a side that changed a field wins it; a
    field both changed goes to `hi` (the larger content hash — arbitrary,
    deterministic, and identical on every replica). Timing fields are
    merged the same way, so the later renewal of an element's expiry
    survives whichever side made it."""
    b = base.data if base is not None and isinstance(base.data, Mapping) else {}
    a, c = lo.data or {}, hi.data or {}
    merged: Dict[str, Any] = dict(b)
    for key in sorted(set(a) | set(c) | set(b)):
        va, vc, vb = a.get(key), c.get(key), b.get(key)
        if key == "payload" and isinstance(va, Mapping) and isinstance(vc, Mapping):
            pb = vb if isinstance(vb, Mapping) else {}
            out: Dict[str, Any] = dict(pb)
            for f in sorted(set(va) | set(vc) | set(pb)):
                fa, fc, fb = va.get(f), vc.get(f), pb.get(f)
                if fa == fc:
                    out[f] = fa
                elif fa == fb:
                    out[f] = fc
                elif fc == fb:
                    out[f] = fa
                else:
                    out[f] = fc
            merged[key] = out
            continue
        if va == vc:
            merged[key] = va
        elif va == vb:
            merged[key] = vc
        elif vc == vb:
            merged[key] = va
        else:
            merged[key] = vc
    return Node(id=lo.id, kind=lo.kind, scope=lo.scope, data=merged, edges=lo.edges)


def open_collaboration(*kinds: str) -> None:
    """Register the field-wise rule for the given element kinds (default:
    the ones this module uses). Call once per process."""
    for k in kinds or ("ui.card", ATTENTION_KIND):
        register_merge(k, fieldwise_merge)


__all__ = [
    "ATTENTION_KIND",
    "MAX_FOLLOW_DEPTH",
    "PARTICIPANT_KIND",
    "PRESENCE_REGION",
    "Focus",
    "Participant",
    "View",
    "attend",
    "attention_id",
    "attention_of",
    "fieldwise_merge",
    "follow",
    "join",
    "open_collaboration",
    "participant_id",
    "participants",
    "present",
    "renew",
    "view_for",
]
