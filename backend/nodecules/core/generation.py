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

from dataclasses import dataclass
from typing import Any, Awaitable, Callable, Dict, List, Literal, Mapping, Optional, Set, Tuple

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
from .strip_access import AllPattern, LatestPattern, RangePattern
from .strip_resolve import range_matches, resolve_range
from .time import TimeRange

RECIPE_ROLE = "recipe"
PARAMS_ROLE = "params"
RECIPE_TEMPLATE_KIND = "recipe.template"

Outcome = Literal["exact", "via-substitute", "equivalent", "lost"]
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
    note: str = ""


def declaration_hash(node: Node) -> str:
    """Identity of what a node *is*, independent of what it currently
    holds: kind and edges. Stable across productions."""
    return canonical_hash({"kind": node.kind, "edges": [e.model_dump(mode="json") for e in node.edges]})


def select(pattern: Any, data: Any, window: Optional[TimeRange]) -> Any:
    """Apply an edge's access pattern to the target's data. `All` is the
    whole value; `Latest` is the last element of a list (or the value);
    `Range` filters a list of elements by the node's window."""
    if isinstance(pattern, AllPattern):
        return data
    if isinstance(pattern, LatestPattern):
        return data[-1] if isinstance(data, list) and data else data
    if isinstance(pattern, RangePattern):
        if window is None:
            raise ValueError("a Range pattern needs the node's window: give it params {'window': {'start_ms', 'end_ms'}}")
        if not isinstance(data, list):
            raise ValueError("a Range pattern reads a list of elements")
        resolved = resolve_range(pattern, window)
        return [el for el in data if range_matches(resolved, el)]
    raise ValueError(f"unknown access pattern {pattern!r}")


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
    ) -> None:
        self.store = store
        if isinstance(realizations, Mapping):
            self.realizations: Dict[str, Realization] = dict(realizations)
        else:
            self.realizations = {r.handle: r for r in realizations}
        self.bindings: Dict[str, str] = dict(bindings or {})
        self.author = author
        self.plan_hash = plan_hash
        self.cooks = 0  # how many times a realization actually ran

    # -- public --------------------------------------------------------------------

    async def produce(self, scope: str, node_id: str) -> Generation:
        """Produce `node_id` in `scope`, producing whatever it depends on
        first. Returns the target's generation; `lost` names the reference
        that could not be recovered."""
        order = self._order(scope, node_id)
        result: Optional[Generation] = None
        for sc, nid in order:
            result = await self._produce_one(sc, nid)
            if result.outcome == "lost" and (sc, nid) != (scope, node_id):
                return Generation(
                    scope=scope,
                    node_id=node_id,
                    outcome="lost",
                    lost=result.lost,
                    note=f"{sc}:{nid} could not be produced",
                )
        assert result is not None
        return result

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
        out: Dict[str, Generation] = {}
        for a in plan.assignments:
            out[a.node_id] = await sub.produce(scope, a.node_id)
        self.cooks += sub.cooks
        return out

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
                    stack.append((child, False))
            else:
                on_path.discard(key)
                seen.add(key)
                order.append(key)
        return order

    # -- one node --------------------------------------------------------------------

    async def _produce_one(self, scope: str, node_id: str) -> Generation:
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
        if realization is None:
            raise NoRealization(f"{node_id} needs realization {used!r}; inventory has {sorted(self.realizations)}")

        inputs: Dict[str, Any] = {}
        resolved: List[ResolvedInput] = []
        lost: List[str] = []
        for e in cur.edges:
            if e.role in (RECIPE_ROLE, PARAMS_ROLE):
                continue
            tgt = self._read(scope, e)
            ref = f"{e.scope or scope}:{e.target}"
            if not isinstance(tgt, Node) or tgt.data is None:
                lost.append(ref)
                continue
            inputs[e.role or e.target] = select(e.pattern, tgt.data, window)
            resolved.append(
                ResolvedInput(
                    role=e.role or e.target,
                    scope=e.scope or scope,
                    name=e.target,
                    content_hash=tgt.content_hash(),
                    pattern=e.pattern.model_dump(mode="json"),
                )
            )
        if lost:
            return Generation(scope=scope, node_id=node_id, outcome="lost", lost=tuple(lost), inputs=tuple(resolved))

        cache_key = canonical_hash(
            {
                "declaration": declaration_hash(cur) if isinstance(cur, Node) else canonical_hash({"kind": cur.kind, "edges": [e.model_dump(mode="json") for e in cur.edges]}),
                "inputs": sorted((r.role, r.scope, r.name, r.content_hash, canonical_hash(r.pattern)) for r in resolved),
                "realization": used,
                "params": params,
            }
        )

        prior_env = self.store.get(manifest, envelope_id(node_id))
        prior = prior_env if isinstance(prior_env, Node) and prior_env.data.get("cache_key") == cache_key else None
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

        data = await realization.cook(inputs, params)
        self.cooks += 1
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
        outcome: Outcome = "via-substitute" if used != declared else "exact"

        recipe_record: Dict[str, Any] = {"realization": used, "declared": declared, "params": params}
        if self.plan_hash is not None:
            recipe_record["plan"] = self.plan_hash
            executor = getattr(self, "_executors", {}).get(node_id)
            if executor is not None:
                recipe_record["executor"] = executor
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
        committed = tx.commit(note=f"produce {node_id}")
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
        )

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
    "Realization",
    "ResolvedInput",
    "declaration_hash",
    "select",
]
