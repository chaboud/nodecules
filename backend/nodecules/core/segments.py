"""Sources that come and go: segments under a produced strip (vault ADR-0039).

The founder, 2026-10-10: "I have a dock at my desk that has a camera
connected. I connect and disconnect. Or a nest camera with transient
power... Both would be strips with dead bands that aren't terminated
forever, or they'd be collections of separate strips that can be logically
merged into a mega strip."

Both, layered:

- **Segments are what gets written.** One strip per connection, named
  `<source>/seg/<n>`. It opens on a fact (the device connected) and closes on
  a fact (the operating system reported the unplug, or a new session proved
  the old one over). Each segment says when it opened and how far it has
  watched, on its own clock, with a map onto the source's timeline: a camera
  that lost power may come back with its clock reset.
- **Coverage is the missing fact.** A segment covers the span from its open
  to the furthest of its last element and its last watch mark (`watch`), so
  "saw nothing" (covered, no element) and "was not looking" (not covered)
  are different. A source that sees nothing calls `watch` instead of
  appending a placeholder.
- **The long strip is produced, not written.** `<source>` is a production
  over every segment (`segments.merge@1`): elements on the source timeline,
  the coverage spans, and, while the newest segment is closed and the source
  not retired, when it went away. Reads against it answer by span: inside a
  covered span, a hit; in a known gap, never ("gap"); past the end of
  coverage, not yet, or provisional under nearest, with the lag recorded.

Silence is not a fact, so a segment that goes quiet stays open; a source
that returns as a new session closes its predecessor at what it last
covered. Backfill (a device uploading what it buffered) is one more segment
over the gap.
"""

from __future__ import annotations

from typing import Any, Dict, List, Optional, Tuple

from .generation import RECIPE_ROLE, RECIPE_TEMPLATE_KIND, Realization
from .store import Edge, Manifest, Node, Store
from .strip_access import AllPattern
from .strip_resolve import head_time
from .timeline import Anchor, Instant, Timebase, Timeline, TimelineMap, convert
from .transforms import Transform, register, strip_data, write, put_value

MERGE_HANDLE = "segments.merge@1"
# A segment is a strip with its own clock and coverage. Its kind is not
# "strip", so a read of it returns the whole record (elements, clock,
# coverage) rather than the bare elements; readers read the long strip.
SEGMENT_KIND = "segment"
MS = Timebase(num=1, den=1000)


def segment_name(source: str, n: int) -> str:
    return f"{source}/seg/{n:04d}"


def index_name(source: str) -> str:
    return f"{source}/segments"


def _segment_state(data: Any) -> Tuple[List[Any], Dict[str, Any], bool]:
    if not isinstance(data, dict) or "segment" not in data:
        raise ValueError("not a segment")
    return list(data.get("elements") or []), dict(data["segment"]), bool(data.get("strip_closed"))


def _seg_data(elements: List[Any], meta: Dict[str, Any], closed: bool) -> Dict[str, Any]:
    d: Dict[str, Any] = {"strip_floor": 0, "elements": list(elements), "segment": meta}
    if closed:
        d["strip_closed"] = True
    return d


def _open(prior: Optional[Any], args: Dict[str, Any]) -> Any:
    if prior is not None:
        raise ValueError("a segment is opened once; a returning source opens the next one")
    meta = {"source": args["source"], "n": args["n"], "opened_ms": args["opened_ms"], "watched_to_ms": args["opened_ms"], "clock": args.get("clock")}
    return _seg_data([], meta, False)


def _watch(prior: Optional[Any], args: Dict[str, Any]) -> Any:
    elements, meta, closed = _segment_state(prior)
    if closed:
        raise ValueError("a closed segment watches nothing")
    meta["watched_to_ms"] = max(int(meta["watched_to_ms"]), int(args["to_ms"]))
    return _seg_data(elements, meta, False)


def _seg_append(prior: Optional[Any], args: Dict[str, Any]) -> Any:
    elements, meta, closed = _segment_state(prior)
    if closed:
        raise ValueError("the segment is closed; a source that comes back opens a new segment")
    return _seg_data([*elements, *args["elements"]], meta, False)


def _seg_append_recover(prior: Optional[Any], new: Any) -> Dict[str, Any]:
    before, _, _ = _segment_state(prior)
    after, _, _ = _segment_state(new)
    return {"elements": after[len(before):]}


def _seg_close(prior: Optional[Any], args: Dict[str, Any]) -> Any:
    elements, meta, closed = _segment_state(prior)
    if closed:
        raise ValueError("the segment is already closed")
    meta["closed_ms"] = int(args["at_ms"])
    meta["closed_because"] = args["because"]
    return _seg_data(elements, meta, True)


SEGMENT_OPEN = register(Transform("segment.open@1", SEGMENT_KIND, _open))
SEGMENT_WATCH = register(Transform("segment.watch@1", SEGMENT_KIND, _watch))
SEGMENT_APPEND = register(Transform("segment.append@1", SEGMENT_KIND, _seg_append, _seg_append_recover))
SEGMENT_CLOSE = register(Transform("segment.close@1", SEGMENT_KIND, _seg_close))


def _covered_to(meta: Dict[str, Any], elements: List[Any], field: str) -> int:
    reach = head_time(elements, field) if elements else None
    to = int(meta["watched_to_ms"])
    if reach is not None:
        to = max(to, reach)
    if "closed_ms" in meta:
        to = max(to, int(meta["closed_ms"]))
    return to


def _index(store: Store, manifest: Manifest, source: str) -> Dict[str, Any]:
    node = store.get(manifest, index_name(source))
    return dict(node.data) if isinstance(node, Node) and isinstance(node.data, dict) else {"count": 0, "open": None, "retired": False}


def current_segment(store: Store, scope: str, source: str) -> Optional[str]:
    idx = _index(store, store.current(scope), source)
    return idx.get("open")


def open_segment(
    store: Store,
    scope: str,
    source: str,
    *,
    at_ms: int,
    offset_ms: int = 0,
    author: str = "",
    field: str = "ts",
) -> str:
    """The source connected (a fact), at `at_ms` on the device's own clock. A
    segment still open is closed first, at what it last covered: the new
    session proves it over. `offset_ms` maps this segment's clock onto the
    source timeline (source = local + offset), as a one-anchor TimelineMap.
    Returns the new segment's name."""
    m = store.current(scope)
    idx = _index(store, m, source)
    if idx.get("retired"):
        raise ValueError(f"{source} is retired")
    prev = idx.get("open")
    if prev is not None:
        node = store.get(m, prev)
        elements, meta, _ = _segment_state(node.data)
        local_to = _covered_to(meta, elements, field)
        write(store, scope, prev, SEGMENT_CLOSE.handle, {"at_ms": local_to, "because": "a new session began"}, author=author, note=f"close {prev}: a new session began")
    n = int(idx.get("count", 0)) + 1
    name = segment_name(source, n)
    clock = TimelineMap(src=f"{name}/clock", dst=f"{source}/clock", anchors=(Anchor(src=0, dst=offset_ms),), provenance="assumed").model_dump(mode="json")
    write(store, scope, name, SEGMENT_OPEN.handle, {"source": source, "n": n, "opened_ms": at_ms, "clock": clock}, author=author, note=f"open {name}")
    put_value(store, scope, index_name(source), "segments.index", {"count": n, "open": name, "retired": False}, author=author, note=f"{source}: segment {n} open")
    _declare_long_strip(store, scope, source, n, field, author)
    return name


def append(store: Store, scope: str, source: str, *elements: Any, author: str = "") -> Manifest:
    """Append observations to the source's open segment (local clock)."""
    seg = current_segment(store, scope, source)
    if seg is None:
        raise ValueError(f"{source} has no open segment; open one when it connects")
    return write(store, scope, seg, SEGMENT_APPEND.handle, {"elements": list(elements)}, author=author, note=f"append {len(elements)} to {seg}")


def watch(store: Store, scope: str, source: str, *, to_ms: int, author: str = "") -> Manifest:
    """The source was watching up to `to_ms` (local clock) and saw nothing new:
    coverage grows, no element is invented."""
    seg = current_segment(store, scope, source)
    if seg is None:
        raise ValueError(f"{source} has no open segment")
    return write(store, scope, seg, SEGMENT_WATCH.handle, {"to_ms": to_ms}, author=author, note=f"{seg} watched to {to_ms}")


def disconnect(store: Store, scope: str, source: str, *, at_ms: int, author: str = "") -> Manifest:
    """The source went away and something reported it (a fact: the operating
    system's unplug event). Closes the open segment at `at_ms` (local clock)."""
    seg = current_segment(store, scope, source)
    if seg is None:
        raise ValueError(f"{source} has no open segment")
    m = write(store, scope, seg, SEGMENT_CLOSE.handle, {"at_ms": at_ms, "because": "disconnected"}, author=author, note=f"close {seg}: disconnected")
    idx = _index(store, m, source)
    return put_value(store, scope, index_name(source), "segments.index", {**idx, "open": None}, author=author, note=f"{source}: away")


def backfill(
    store: Store,
    scope: str,
    source: str,
    *elements: Any,
    from_ms: int,
    to_ms: int,
    offset_ms: int = 0,
    author: str = "",
    field: str = "ts",
) -> str:
    """What a device buffered while disconnected and uploaded on return: one
    more segment, closed, covering [from_ms, to_ms) on its own clock. The
    open segment, if any, is untouched."""
    m = store.current(scope)
    idx = _index(store, m, source)
    n = int(idx.get("count", 0)) + 1
    name = segment_name(source, n)
    clock = TimelineMap(src=f"{name}/clock", dst=f"{source}/clock", anchors=(Anchor(src=0, dst=offset_ms),), provenance="assumed").model_dump(mode="json")
    write(store, scope, name, SEGMENT_OPEN.handle, {"source": source, "n": n, "opened_ms": from_ms, "clock": clock}, author=author, note=f"open {name}: backfill")
    if elements:
        write(store, scope, name, SEGMENT_APPEND.handle, {"elements": list(elements)}, author=author, note=f"backfill {len(elements)} into {name}")
    write(store, scope, name, SEGMENT_CLOSE.handle, {"at_ms": to_ms, "because": "backfill"}, author=author, note=f"close {name}: backfill")
    put_value(store, scope, index_name(source), "segments.index", {**_index(store, store.current(scope), source), "count": n}, author=author, note=f"{source}: backfill segment {n}")
    _declare_long_strip(store, scope, source, n, field, author)
    return name


def retire(store: Store, scope: str, source: str, *, author: str = "") -> Manifest:
    """The source will not come back: the long strip closes."""
    seg = current_segment(store, scope, source)
    if seg is not None:
        node = store.get(store.current(scope), seg)
        elements, meta, _ = _segment_state(node.data)
        write(store, scope, seg, SEGMENT_CLOSE.handle, {"at_ms": _covered_to(meta, elements, "ts"), "because": "retired"}, author=author, note=f"close {seg}: retired")
    idx = _index(store, store.current(scope), source)
    return put_value(store, scope, index_name(source), "segments.index", {**idx, "open": None, "retired": True}, author=author, note=f"{source}: retired")


def _declare_long_strip(store: Store, scope: str, source: str, count: int, field: str, author: str) -> None:
    edges = [Edge(target=segment_name(source, i), pattern=AllPattern(), role=f"seg{i:04d}") for i in range(1, count + 1)]
    edges.append(Edge(target=index_name(source), role="index"))
    edges.append(Edge(target=f"{source}/merge", role=RECIPE_ROLE))
    tx = store.transaction(scope, author=author)
    if store.get(tx.base, f"{source}/merge") is None:
        tx.put(Node(id=f"{source}/merge", kind=RECIPE_TEMPLATE_KIND, scope=scope, data={"realization": MERGE_HANDLE, "params": {"field": field}}))
    tx.put(Node(id=source, kind="strip", scope=scope, edges=tuple(edges)))
    tx.commit(note=f"{source}: the long strip reads {count} segment(s)")


def _to_source(t: int, clock: Dict[str, Any]) -> int:
    via = TimelineMap.model_validate(clock)
    timelines = {via.src: Timeline(id=via.src, timebase=MS, origin="device-boot"), via.dst: Timeline(id=via.dst, timebase=MS, origin="source-start")}
    return convert(Instant(ticks=t, timeline=via.src), via, timelines).instant.ticks


def _shift(el: Any, field: str, clock: Dict[str, Any], n: int) -> Any:
    if not isinstance(el, dict) or not isinstance(el.get(field), dict):
        return {"value": el, "segment": n}
    span = el[field]
    out = dict(el)
    out[field] = {**span, "start_ms": _to_source(int(span["start_ms"]), clock), "end_ms": _to_source(int(span["end_ms"]), clock)}
    out["segment"] = n
    return out


async def _merge(inputs: Dict[str, Any], params: Dict[str, Any]) -> Any:
    field = params.get("field", "ts")
    idx = inputs.get("index") or {}
    spans: List[List[int]] = []
    elements: List[Any] = []
    for role in sorted(k for k in inputs if k.startswith("seg")):
        elems, meta, closed = _segment_state(inputs[role])
        clock = meta["clock"]
        a = _to_source(int(meta["opened_ms"]), clock)
        b = _to_source(_covered_to(meta, elems, field), clock)
        spans.append([a, b])
        elements.extend(_shift(e, field, clock, int(meta["n"])) for e in elems)
    spans.sort()
    coverage: List[List[int]] = []
    for a, b in spans:
        if coverage and a <= coverage[-1][1]:
            coverage[-1][1] = max(coverage[-1][1], b)
        else:
            coverage.append([a, b])
    elements.sort(key=lambda e: (e.get(field, {}).get("start_ms", 0) if isinstance(e, dict) and isinstance(e.get(field), dict) else 0))
    out: Dict[str, Any] = {"strip_floor": 0, "elements": elements, "coverage": coverage, "coverage_field": field}
    if idx.get("retired"):
        out["strip_closed"] = True
    elif idx.get("open") is None and coverage:
        out["away_since_ms"] = coverage[-1][1]  # nothing is watching now; the last span watched ended here
    return out


def merge_realization() -> Realization:
    """The long strip's realization: segments onto one timeline, with coverage."""
    return Realization(MERGE_HANDLE, _merge, deterministic=True)


__all__ = [
    "MERGE_HANDLE",
    "SEGMENT_APPEND",
    "SEGMENT_CLOSE",
    "SEGMENT_OPEN",
    "SEGMENT_WATCH",
    "append",
    "backfill",
    "current_segment",
    "disconnect",
    "index_name",
    "merge_realization",
    "open_segment",
    "retire",
    "segment_name",
    "watch",
]
