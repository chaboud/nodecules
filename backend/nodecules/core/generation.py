"""Generation — produce node X by running its recipe over the nodes X
references (REFERENCE-MODEL §6), over the store. PR-r5a: the smallest
honest form, and the canary: the piece every consumer stands on.

One operation covers cold start (X is declared, its data is None), stale
recook (an input changed), and resurrection (X's data was pruned and its
envelope says how to rebuild it). Only the trigger differs.

The shapes this slice fixes:

- **A node's declaration is its kind and its edges; its output is its
  data.** Inputs are edges with roles; the recipe is an edge whose role is
  `recipe` pointing at a `recipe.template` node (`{"realization": handle,
  "params": {...}}`); per-instance parameters are an optional edge whose
  role is `params` pointing at a node whose data merges over the
  template's params. Everything a production depends on is therefore a
  node, content-addressed, and nothing is hidden in the engine.
- **The cache key is the declaration composed with what it actually read**:
  `hash(declaration, resolved inputs, realization, params)`. Declarations
  stay symbolic (patterns on edges); what a pattern resolved to is
  recorded here and in the envelope, never written back into the node.
- **The envelope is the receipt of production.** It records the
  realization declared and the one used, the params, the inputs by
  identity, the cache key, the outcome, the reproducibility, and whether
  reproducibility was *measured* (a re-production with the same cache key)
  or merely *declared* (a first production by a realization that calls
  itself deterministic). Defaulting to perturbing (ADR-0009): a
  realization that does not declare determinism is `equivalent`.
- **Outcomes** (§6): `exact` and `via-substitute` are the identity axis
  (was the realization the one declared?); `equivalent` is the
  reproducibility axis; `lost` is when a reference is unrecoverable. A
  realization that declared determinism and reproduced a different hash is
  a **falsified stated claim** (ADR-0021 refinement) and is flagged, not
  hidden.
- **A plan can be dispatched.** `dispatch(plan)` takes the placement
  plan's assignments as bindings (node → realization) and produces each
  node through them, recording the plan's hash in every envelope. Where the
  bound realization differs from the declared one the outcome is
  `via-substitute`, which is the receipt ADR-0010 requires.

Not here, on purpose: running on another executor (the executor field of
an assignment is recorded, not acted on), routing kinds (§18), retention
(nothing here prunes), and the legacy `BaseNode` adapter — a consumer
wraps its own nodes as `Realization`s.
"""

from __future__ import annotations

import asyncio

from dataclasses import dataclass
from typing import Any, Awaitable, Callable, Dict, Iterable, List, Literal, Mapping, Optional, Set, Tuple

from pydantic import BaseModel, ConfigDict

from .store import (
    Absent,
    Cycle,
    DanglingEdge,
    Edge,
    Lost,
    Manifest,
    Node,
    Store,
    canonical_hash,
    envelope_id,
    make_envelope,
)
from .strip_access import AllPattern, FromHead, FromSelf, Index, LatestPattern, OrdinalPattern, RangePattern
from .strip_resolve import earliest_time, head_time, needs_head, range_matches, resolve_range, resolve_time_expr
from .time import TimeRange

RECIPE_ROLE = "recipe"
PARAMS_ROLE = "params"
RECIPE_TEMPLATE_KIND = "recipe.template"

Outcome = Literal["exact", "via-substitute", "equivalent", "lost", "failed", "pending"]
Reproducibility = Literal["exact", "equivalent"]

CookFn = Callable[[Dict[str, Any], Dict[str, Any]], Awaitable[Any]]


@dataclass(frozen=True)
class Realization:
    """A concrete way to cook a kind: a handle (its identity — a name and
    version, or a content hash of its code), whether it declares itself
    deterministic (unknown is perturbing), and the cook function
    `(inputs by role, params) -> data`."""

    handle: str
    cook: CookFn
    deterministic: bool = False
    # At most this many cooks of this realization run at once (None: no limit).
    # A model server with one slot, or one shared with another system, says 1.
    concurrency: Optional[int] = None


class Routed:
    """What a routing realization returns: the chosen alternative's data,
    which role it chose, and which role was the primary. The generator
    unwraps it, records the route in the receipt, and marks the outcome
    `via-substitute` when the choice was not the primary (REFERENCE-MODEL
    §18: routing is graph structure, and a substitution is visible)."""

    __slots__ = ("data", "chose", "primary")

    def __init__(self, data: Any, *, chose: str, primary: str) -> None:
        self.data = data
        self.chose = chose
        self.primary = primary


def router_realization(handle: str = "router.first-available@1") -> Realization:
    """A router: its inputs are alternatives (usually optional edges); params
    `prefer` lists roles in order, first is the primary. Picks the first
    alternative that is present. With none present it fails ordinarily."""

    async def cook(inputs: Dict[str, Any], params: Dict[str, Any]) -> Any:
        order = list(params.get("prefer") or sorted(inputs))
        if not order:
            raise RuntimeError("router has no alternatives")
        for role in order:
            if role in inputs and inputs[role] is not None:
                return Routed(inputs[role], chose=role, primary=order[0])
        raise RuntimeError(f"no alternative available among {order}")

    return Realization(handle=handle, cook=cook, deterministic=True)


class NoRealization(Exception):
    """The recipe names a realization this generator does not have, and no
    binding supplied a substitute. Fails ordinarily (E_NOINTERFACE)."""


class ResolvedInput(BaseModel):
    """What one edge resolved to at production time."""

    model_config = ConfigDict(frozen=True)

    role: str
    scope: str
    name: str
    content_hash: str
    pattern: Dict[str, Any]
    # The hash of what was read, when the pattern selected part of the node
    # (latest, a window, a slice). The cache key uses it, so an append outside
    # a reader's window is a cache hit. None for a whole read: the node's hash.
    read_hash: Optional[str] = None


class Generation(BaseModel):
    """What producing one node did, and what it produced."""

    model_config = ConfigDict(frozen=True, arbitrary_types_allowed=True)

    scope: str
    node_id: str
    outcome: Outcome
    reproducibility: Optional[Reproducibility] = None
    measured: bool = False
    cache_hit: bool = False
    cooked: bool = False
    falsified_determinism: bool = False
    node: Optional[Node] = None
    envelope: Optional[Node] = None
    manifest: Optional[Manifest] = None
    cache_key: Optional[str] = None
    realization: Optional[str] = None
    declared_realization: Optional[str] = None
    inputs: Tuple[ResolvedInput, ...] = ()
    lost: Tuple[str, ...] = ()
    omitted: Tuple[str, ...] = ()  # optional inputs that were not available
    route: Optional[Dict[str, str]] = None  # {"chose": role, "primary": role} when a router ran
    attempts: int = 0
    error: Optional[str] = None
    request: Optional[str] = None  # the request node written when the production was deferred
    note: str = ""


def declaration_hash(node: Node) -> str:
    """Identity of what a node *is*, independent of what it currently
    holds: kind and edges. Stable across productions."""
    return canonical_hash({"kind": node.kind, "edges": [e.model_dump(mode="json") for e in node.edges]})


class Read:
    """What one edge's pattern took from its target: the value, the hash of
    what was read (None for a whole read), how the symbolic pattern resolved
    (for the receipt), and why the read could not be served: `lost` when it
    needs elements the strip released, `waiting` when it needs elements that
    do not exist yet."""

    __slots__ = ("value", "read_hash", "resolved", "lost", "waiting")

    def __init__(self, value: Any = None, read_hash: Optional[str] = None, resolved: Optional[Dict[str, Any]] = None, lost: Optional[str] = None, waiting: Optional[str] = None) -> None:
        self.value = value
        self.read_hash = read_hash
        self.resolved = resolved
        self.lost = lost
        self.waiting = waiting


def _strip_view(data: Any, kind: Optional[str]) -> Tuple[int, Any]:
    if kind == "strip" and isinstance(data, dict) and "strip_floor" in data and isinstance(data.get("elements"), list):
        return int(data["strip_floor"]), data["elements"]
    return 0, data


def read(pattern: Any, data: Any, window: Optional[TimeRange], position: Optional[int] = None, kind: Optional[str] = None) -> Read:
    """Apply an edge's access pattern to its target's data. `All` is the whole
    value; `Latest` the last element of a list (or the value); `Range` the
    elements whose time field meets a window, the window relative to the
    reader's or to the strip's head; `Ordinal` a slice by position, relative
    to the head or to the reader's `position`. The relative forms only look
    back, and a read into released elements is lost, never silently short."""
    floor, data = _strip_view(data, kind)
    if isinstance(pattern, AllPattern):
        if floor:
            return Read(lost=f"an All read needs every element, and elements [0, {floor}) were released; read a window (last(n), or a range) instead")
        return Read(value=data)
    if isinstance(pattern, LatestPattern):
        value = data[-1] if isinstance(data, list) and data else data
        return Read(value=value, read_hash=canonical_hash(value))
    if isinstance(pattern, RangePattern):
        if not isinstance(data, list):
            raise ValueError("a Range pattern reads a list of elements")
        head = head_time(data, pattern.field) if (needs_head(pattern.start) or needs_head(pattern.end)) else None
        if (needs_head(pattern.start) or needs_head(pattern.end)) and head is None:
            return Read(value=[], read_hash=canonical_hash([]), resolved={"pattern": "range", "field": pattern.field, "window": None, "count": 0, "note": "no head: the strip has no element with this field"})
        start = _resolve_bound(pattern.start, window, head)
        end = _resolve_bound(pattern.end, window, head)
        if end <= start:
            return Read(value=[], read_hash=canonical_hash([]), resolved={"pattern": "range", "field": pattern.field, "window": [start, end], "count": 0})
        resolved = resolve_range(pattern, window, head)
        if floor:
            earliest = earliest_time(data, pattern.field)
            if earliest is None or resolved.window.start_ms < earliest:
                return Read(lost=f"the window starts at {resolved.window.start_ms}, before the earliest retained element ({earliest}); elements [0, {floor}) were released")
        value = [el for el in data if range_matches(resolved, el)]
        return Read(value=value, read_hash=canonical_hash(value), resolved={"pattern": "range", "field": pattern.field, "window": [resolved.window.start_ms, resolved.window.end_ms], "count": len(value)})
    if isinstance(pattern, OrdinalPattern):
        if not isinstance(data, list):
            raise ValueError("an Ordinal pattern reads a list of elements")
        total = floor + len(data)
        start = _resolve_position(pattern.start, total, position)
        end = _resolve_position(pattern.end, total, position)
        if end > total:
            return Read(waiting=f"element {end - 1} does not exist yet (the strip has {total})")
        start = max(0, start)
        resolved = {"pattern": "ordinal", "start": start, "end": max(start, end), "of": total}
        if floor:
            resolved["floor"] = floor
        if end <= start:
            return Read(value=[], read_hash=canonical_hash([]), resolved={**resolved, "count": 0})
        if start < floor:
            return Read(lost=f"elements [{start}, {floor}) were released (the floor is {floor})")
        value = data[start - floor : end - floor]
        return Read(value=value, read_hash=canonical_hash(value), resolved={**resolved, "count": len(value)})
    raise ValueError(f"unknown access pattern {pattern!r}")


def _resolve_bound(expr: Any, window: Optional[TimeRange], head: Optional[int]) -> int:
    return resolve_time_expr(expr, window, head)


def _resolve_position(expr: Any, total: int, position: Optional[int]) -> int:
    if isinstance(expr, FromHead):
        return total + expr.offset
    if isinstance(expr, Index):
        return expr.index
    if isinstance(expr, FromSelf):
        if position is None:
            raise ValueError("this read is relative to the reader's position: give the node params {'position': n}")
        return int(position) + expr.offset
    raise TypeError(f"unhandled ordinal expression {type(expr).__name__}")


def select(pattern: Any, data: Any, window: Optional[TimeRange]) -> Any:
    """The value an access pattern takes from data (see `read`); raises
    when the read cannot be served."""
    r = read(pattern, data, window)
    if r.lost is not None:
        raise LookupError(r.lost)
    if r.waiting is not None:
        raise LookupError(r.waiting)
    return r.value


def _window_from(params: Mapping[str, Any]) -> Optional[TimeRange]:
    w = params.get("window")
    if w is None:
        return None
    if isinstance(w, TimeRange):
        return w
    return TimeRange(**w)


class Generator:
    """Produces nodes through a store from an inventory of realizations.
    `bindings` (node id → realization handle) come from a plan and may
    substitute the declared realization; `author` is recorded on every
    manifest the generator commits."""

    def __init__(
        self,
        store: Store,
        realizations: Mapping[str, Realization] | Tuple[Realization, ...] | List[Realization],
        *,
        bindings: Optional[Mapping[str, str]] = None,
        author: str = "generator",
        plan_hash: Optional[str] = None,
        tracker: Any = None,
        defer: bool = False,
    ) -> None:
        self.defer = defer  # a missing realization becomes a request node instead of an error (core/deferral.py)
        self.tracker = tracker  # core/tracking.py's Tracker, or anything with .record(kind, scope, node, **detail)
        self.store = store
        if isinstance(realizations, Mapping):
            self.realizations: Dict[str, Realization] = dict(realizations)
        else:
            self.realizations = {r.handle: r for r in realizations}
        self.bindings: Dict[str, str] = dict(bindings or {})
        self.author = author
        self.plan_hash = plan_hash
        self.cooks = 0  # how many times a realization actually ran
        # Productions running now, so two calls that need the same node share
        # one cook instead of racing (REFERENCE-MODEL §22: one producer per node).
        self._inflight: Dict[Tuple[str, str], "asyncio.Task[Generation]"] = {}
        self._limits: Dict[Tuple[str, int], asyncio.Semaphore] = {}  # (handle, loop id) -> its concurrency cap

    # -- public --------------------------------------------------------------------

    async def produce(self, scope: str, node_id: str) -> Generation:
        """Produce `node_id` in `scope`, producing whatever it depends on
        first. Returns the target's generation; `lost` names the reference
        that could not be recovered."""
        out = await self.produce_many([(scope, node_id)])
        return out[(scope, node_id)]

    async def produce_many(self, targets: Iterable[Tuple[str, str]]) -> Dict[Tuple[str, str], Generation]:
        """Produce several targets, each node of their dependency graphs
        once, cooking every node as soon as its inputs are ready: independent
        branches cook at the same time.

        Concurrency here is cooperative, in one thread. Cooks overlap while
        they wait (a model call, a remote fetch); every commit is still a
        synchronous critical section, created and committed with no await in
        between, so a transaction's base is never stale and writes to
        disjoint names never conflict (REFERENCE-MODEL §22). A realization
        that declares `concurrency` is capped at that many cooks at once. A
        node another call is already producing is awaited, not cooked twice.

        If a node raises (a dangling edge, no realization and no deferral),
        the targets that depend on it raise; the rest of this call's
        productions are allowed to finish first, so nothing is left running
        unobserved."""
        wanted = [(sc, nid) for sc, nid in targets]
        order: List[Tuple[str, str]] = []
        placed: Set[Tuple[str, str]] = set()
        for sc, nid in wanted:
            for key in self._order(sc, nid):
                if key not in placed:
                    placed.add(key)
                    order.append(key)  # post-order per target, so a node's inputs are placed before it
        tasks: Dict[Tuple[str, str], "asyncio.Task[Generation]"] = {}
        for key in order:
            node = self.store.get(self.store.current(key[0]), key[1])
            deps = []
            for e in node.edges if node is not None else ():
                child = (e.scope or key[0], e.target)
                if child in tasks and child not in deps:
                    deps.append(child)
            tasks[key] = self._start(key, [(d, tasks[d]) for d in deps])
        try:
            return {key: await tasks[key] for key in wanted}
        finally:
            await asyncio.gather(*tasks.values(), return_exceptions=True)

    def _start(self, key: Tuple[str, str], deps: List[Tuple[Tuple[str, str], "asyncio.Task[Generation]"]]) -> "asyncio.Task[Generation]":
        running = self._inflight.get(key)
        loop = asyncio.get_running_loop()
        if running is not None and not running.done() and running.get_loop() is loop:
            return running
        task = loop.create_task(self._produce_after(key, deps))
        self._inflight[key] = task

        def _forget(t: "asyncio.Task[Generation]", key: Tuple[str, str] = key) -> None:
            if self._inflight.get(key) is t:
                del self._inflight[key]

        task.add_done_callback(_forget)
        return task

    async def _produce_after(self, key: Tuple[str, str], deps: List[Tuple[Tuple[str, str], "asyncio.Task[Generation]"]]) -> Generation:
        problems: Dict[str, Generation] = {}  # ref -> the lost, failed, or pending input, so consumers carry the root cause
        for (dsc, dnid), task in deps:
            g = await task
            if g.outcome in ("failed", "lost", "pending"):
                problems[f"{dsc}:{dnid}"] = g
        result = await self._produce_one(key[0], key[1], problems)
        self._track(result)
        return result

    def _limit(self, realization: Realization) -> Optional[asyncio.Semaphore]:
        if realization.concurrency is None:
            return None
        loop = asyncio.get_running_loop()
        k = (realization.handle, id(loop))
        sem = self._limits.get(k)
        if sem is None:
            sem = self._limits[k] = asyncio.Semaphore(max(1, int(realization.concurrency)))
        return sem

    async def dispatch(self, plan: Any, scope: str) -> Dict[str, Generation]:
        """Run a placement plan: its assignments become bindings, every
        envelope records the plan's hash, and each assigned node is
        produced. The executor an assignment names is recorded in the
        envelope and not acted on — moving compute is a later slice."""
        bindings = {a.node_id: a.realization for a in plan.assignments}
        executors = {a.node_id: a.executor_id for a in plan.assignments}
        sub = Generator(
            self.store,
            self.realizations,
            bindings=bindings,
            author=self.author,
            plan_hash=plan.content_hash(),
        )
        sub._executors = executors  # type: ignore[attr-defined]
        produced = await sub.produce_many([(scope, a.node_id) for a in plan.assignments])
        self.cooks += sub.cooks
        return {a.node_id: produced[(scope, a.node_id)] for a in plan.assignments}

    def _track(self, g: Generation) -> None:
        if self.tracker is None:
            return
        how = "cache-hit" if g.cache_hit else ("cooked" if g.cooked else g.outcome)
        if g.outcome == "pending" and g.note == "already requested":
            return
        self.tracker.record(
            f"produce.{how}",
            g.scope,
            g.node_id,
            outcome=g.outcome,
            reproducibility=g.reproducibility,
            realization=g.realization,
            cache_key=g.cache_key,
            route=g.route,
            omitted=list(g.omitted),
            lost=list(g.lost),
            attempts=g.attempts if g.attempts > 1 else None,
            error=g.error,
        )

    # -- ordering --------------------------------------------------------------------

    def _order(self, scope: str, node_id: str) -> List[Tuple[str, str]]:
        """Post-order over the dependency graph from the target: every node
        that has a recipe is produced after its inputs. Iterative; a strip
        is an unbounded chain."""
        root = (scope, node_id)
        order: List[Tuple[str, str]] = []
        seen: Set[Tuple[str, str]] = set()
        on_path: Set[Tuple[str, str]] = set()
        stack: List[Tuple[Tuple[str, str], bool]] = [(root, False)]
        while stack:
            key, expanded = stack.pop()
            if key in seen:
                continue
            sc, nid = key
            cur = self.store.get(self.store.current(sc), nid)
            if cur is None:
                raise DanglingEdge(f"{sc}:{nid} is not bound")
            edges: Tuple[Edge, ...] = cur.edges if not isinstance(cur, Lost) else cur.edges
            if not expanded:
                on_path.add(key)
                stack.append((key, True))
                for e in edges:
                    child = (e.scope or sc, e.target)
                    if child in seen:
                        continue
                    if child in on_path:
                        raise Cycle(f"{child[0]}:{child[1]} reaches itself")
                    if e.optional and self.store.get(self.store.current(child[0]), child[1]) is None:
                        continue  # an optional input that does not exist yet is simply omitted
                    stack.append((child, False))
            else:
                on_path.discard(key)
                seen.add(key)
                order.append(key)
        return order

    # -- one node --------------------------------------------------------------------

    async def _produce_one(self, scope: str, node_id: str, problems: Optional[Dict[str, Generation]] = None) -> Generation:
        problems = problems or {}
        manifest = self.store.current(scope)
        cur = self.store.get(manifest, node_id)
        if cur is None:
            raise DanglingEdge(f"{scope}:{node_id} is not bound")
        if isinstance(cur, Lost):
            return Generation(scope=scope, node_id=node_id, outcome="lost", lost=(f"{scope}:{node_id}",))

        recipe_edge = next((e for e in cur.edges if e.role == RECIPE_ROLE), None)
        params_edge = next((e for e in cur.edges if e.role == PARAMS_ROLE), None)

        if recipe_edge is None:
            # A source: nothing cooks it. Present if its data is here.
            if isinstance(cur, Node) and cur.data is not None:
                return Generation(scope=scope, node_id=node_id, outcome="exact", cache_hit=True, node=cur, note="source")
            return Generation(scope=scope, node_id=node_id, outcome="lost", lost=(f"{scope}:{node_id}",), note="source without data")

        template = self._read(scope, recipe_edge)
        if not isinstance(template, Node) or template.data is None:
            return Generation(scope=scope, node_id=node_id, outcome="lost", lost=(f"{recipe_edge.scope or scope}:{recipe_edge.target}",), note="recipe template unavailable")
        params: Dict[str, Any] = dict(template.data.get("params") or {})
        if params_edge is not None:
            p = self._read(scope, params_edge)
            if not isinstance(p, Node) or p.data is None:
                return Generation(scope=scope, node_id=node_id, outcome="lost", lost=(f"{params_edge.scope or scope}:{params_edge.target}",), note="params unavailable")
            params.update(p.data)
        window = _window_from(params)

        declared = template.data["realization"]
        used = self.bindings.get(node_id, declared)
        realization = self.realizations.get(used)
        if realization is None and not self.defer:
            raise NoRealization(f"{node_id} needs realization {used!r}; inventory has {sorted(self.realizations)}")

        inputs: Dict[str, Any] = {}
        resolved: List[ResolvedInput] = []
        lost: List[str] = []
        omitted: List[str] = []
        causes: List[str] = []
        reads: Dict[str, Dict[str, Any]] = {}  # ref -> how a relative or windowed read resolved, for the receipt
        position = params.get("position")
        for e in cur.edges:
            if e.role in (RECIPE_ROLE, PARAMS_ROLE):
                continue
            tgt = self._read(scope, e)
            ref = f"{e.scope or scope}:{e.target}"
            upstream = problems.get(ref)
            if upstream is not None or not isinstance(tgt, Node) or tgt.data is None:
                # unavailable now, or produced just now and failed/lost: a stale body does not count
                if e.optional:
                    omitted.append(ref)
                elif upstream is not None and upstream.outcome == "pending":
                    return Generation(scope=scope, node_id=node_id, outcome="pending", note=f"waiting on {ref}", inputs=tuple(resolved))
                elif upstream is not None and upstream.outcome == "failed":
                    return Generation(scope=scope, node_id=node_id, outcome="failed", error=f"input {ref} failed: {upstream.error}", inputs=tuple(resolved))
                elif upstream is not None:
                    lost.extend(upstream.lost or (ref,))  # carry the root cause, not the neighbour
                else:
                    lost.append(ref)
                continue
            r = read(e.pattern, tgt.data, window, position, tgt.kind)
            if r.waiting is not None:
                if e.optional:
                    omitted.append(ref)
                    continue
                return Generation(scope=scope, node_id=node_id, outcome="pending", note=f"waiting on {ref}: {r.waiting}", inputs=tuple(resolved))
            if r.lost is not None:
                if e.optional:
                    omitted.append(ref)
                else:
                    lost.append(ref)
                    causes.append(f"{ref}: {r.lost}")
                continue
            inputs[e.role or e.target] = r.value
            if r.resolved is not None:
                reads[ref] = r.resolved
            resolved.append(
                ResolvedInput(
                    role=e.role or e.target,
                    scope=e.scope or scope,
                    name=e.target,
                    content_hash=tgt.content_hash(),
                    pattern=e.pattern.model_dump(mode="json"),
                    read_hash=r.read_hash,
                )
            )
        if lost:
            return Generation(scope=scope, node_id=node_id, outcome="lost", lost=tuple(dict.fromkeys(lost)), inputs=tuple(resolved), note="; ".join(causes))

        cache_key = canonical_hash(
            {
                "declaration": declaration_hash(cur) if isinstance(cur, Node) else canonical_hash({"kind": cur.kind, "edges": [e.model_dump(mode="json") for e in cur.edges]}),
                "inputs": sorted((r.role, r.scope, r.name, r.read_hash or r.content_hash, canonical_hash(r.pattern)) for r in resolved),
                "realization": used,
                "params": params,
            }
        )

        prior_env = self.store.get(manifest, envelope_id(node_id))
        prior = prior_env if isinstance(prior_env, Node) and prior_env.data.get("cache_key") == cache_key else None
        if realization is None and not (prior is not None and isinstance(cur, Node) and cur.data is not None):
            return self._defer(scope, node_id, used, declared, resolved, params, cache_key, manifest)
        if prior is not None and isinstance(cur, Node) and cur.data is not None:
            return Generation(
                scope=scope,
                node_id=node_id,
                outcome=prior.data["outcome"],
                reproducibility=prior.data.get("reproducibility"),
                measured=bool(prior.data.get("measured")),
                cache_hit=True,
                node=cur,
                envelope=prior,
                manifest=manifest,
                cache_key=cache_key,
                realization=used,
                declared_realization=declared,
                inputs=tuple(resolved),
            )

        attempts = 0
        max_attempts = 1 + int(params.get("retry", 0) or 0)
        route: Optional[Dict[str, str]] = None
        while True:
            attempts += 1
            try:
                limit = self._limit(realization)
                if limit is None:
                    data = await realization.cook(inputs, params)
                else:
                    async with limit:
                        data = await realization.cook(inputs, params)
                self.cooks += 1
                break
            except Exception as exc:  # a realization failed: a state, not a crash
                if attempts >= max_attempts:
                    return Generation(
                        scope=scope,
                        node_id=node_id,
                        outcome="failed",
                        realization=used,
                        declared_realization=declared,
                        inputs=tuple(resolved),
                        omitted=tuple(omitted),
                        attempts=attempts,
                        error=f"{type(exc).__name__}: {exc}",
                    )
        if isinstance(data, Routed):
            route = {"chose": data.chose, "primary": data.primary}
            data = data.data
        produced = Node(id=node_id, kind=cur.kind, scope=scope, data=data, edges=cur.edges)
        new_hash = produced.content_hash()

        falsified = False
        if prior is not None:
            # Same cache key, cooked again: a measurement of reproducibility.
            measured = True
            same = new_hash == prior.data["content_hash"]
            if realization.deterministic and not same:
                falsified = True
            reproducibility: Reproducibility = "exact" if (realization.deterministic and same) else "equivalent"
        else:
            measured = False
            reproducibility = "exact" if realization.deterministic else "equivalent"
        outcome: Outcome = "via-substitute" if (used != declared or (route is not None and route["chose"] != route["primary"])) else "exact"

        recipe_record: Dict[str, Any] = {"realization": used, "declared": declared, "params": params}
        if route is not None:
            recipe_record["route"] = route
        if omitted:
            recipe_record["omitted"] = list(omitted)
        if reads:
            recipe_record["reads"] = reads
        if attempts > 1:
            recipe_record["attempts"] = attempts
        if self.plan_hash is not None:
            recipe_record["plan"] = self.plan_hash
            executor = getattr(self, "_executors", {}).get(node_id)
            if executor is not None:
                recipe_record["executor"] = executor
        # A request written for this node by a producer that lacked the realization
        # (core/deferral.py) is answered by this production: clear it in the same
        # commit and say who did.
        from .deferral import request_id

        rid = request_id(node_id)
        pending_request = isinstance(self.store.get(manifest, rid), Node)
        if pending_request:
            recipe_record["fulfilled_by"] = self.author
            recipe_record["request"] = rid
        envelope = make_envelope(
            produced,
            recipe=recipe_record,
            inputs={f"{r.scope}:{r.name}": r.content_hash for r in resolved},
            cache_key=cache_key,
            outcome=outcome,
            reproducibility=reproducibility,
            measured=measured,
            falsified_determinism=falsified,
        )
        tx = self.store.transaction(scope, author=self.author)
        tx.put(produced)
        tx.put(envelope)
        if pending_request:
            tx.delete(rid)
        committed = tx.commit(note=f"fulfil {node_id} ({self.author})" if pending_request else f"produce {node_id}")
        return Generation(
            scope=scope,
            node_id=node_id,
            outcome=outcome,
            reproducibility=reproducibility,
            measured=measured,
            cooked=True,
            falsified_determinism=falsified,
            node=produced,
            envelope=envelope,
            manifest=committed,
            cache_key=cache_key,
            realization=used,
            declared_realization=declared,
            inputs=tuple(resolved),
            omitted=tuple(omitted),
            route=route,
            attempts=attempts,
        )

    def _defer(self, scope: str, node_id: str, used: str, declared: str, resolved: List[ResolvedInput], params: Dict[str, Any], cache_key: str, manifest: Manifest) -> Generation:
        """No realization here: write a request node for whoever has one."""
        from .deferral import REQUEST_KIND, request_id

        rid = request_id(node_id)
        existing = self.store.get(manifest, rid)
        payload = {
            "for": node_id,
            "realization": used,
            "declared": declared,
            "inputs": {f"{r.scope}:{r.name}": r.content_hash for r in resolved},
            "params": params,
            "cache_key": cache_key,
            "status": "pending",
        }
        if isinstance(existing, Node) and existing.data == payload:
            return Generation(scope=scope, node_id=node_id, outcome="pending", request=rid, cache_key=cache_key, realization=used, declared_realization=declared, inputs=tuple(resolved), note="already requested")
        tx = self.store.transaction(scope, author=self.author)
        tx.put(Node(id=rid, kind=REQUEST_KIND, scope=scope, data=payload))
        committed = tx.commit(note=f"request {node_id}")
        return Generation(scope=scope, node_id=node_id, outcome="pending", request=rid, cache_key=cache_key, realization=used, declared_realization=declared, inputs=tuple(resolved), manifest=committed)

    def _read(self, from_scope: str, edge: Edge):
        scope = edge.scope or from_scope
        return self.store.get(self.store.current(scope), edge.target)


__all__ = [
    "Generation",
    "Generator",
    "NoRealization",
    "PARAMS_ROLE",
    "RECIPE_ROLE",
    "RECIPE_TEMPLATE_KIND",
    "Read",
    "Realization",
    "ResolvedInput",
    "Routed",
    "router_realization",
    "declaration_hash",
    "read",
    "select",
]
