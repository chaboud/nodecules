"""Change-driven production: a reactor keeps a set of target nodes fresh.

When a commit or a head move changes anything a target reads (its inputs,
their inputs, and the recipes and params on the way, followed transitively
through edges), the targets are produced again. Nobody has to ask. The
generator does the rest: an input that did not really change is a cache hit,
so a spurious trigger costs a resolve, not a cook.

How a change is noticed. The reactor listens to the store (`Store.listeners`,
which fire outside the store's lock after a commit or a head move). For each
scope it reads, it keeps the last manifest it saw and diffs the new one
against it, so it sees every changed name whether the change came from a
commit here, a replica merge, or a pull from a peer (`core/transport.py`).
It keeps no index of the whole store: the set of names a target reads is
recomputed after every run, so a declaration that gains an edge is followed
from then on, and an input that does not exist yet is watched so its arrival
triggers.

What it ignores. Commits authored by its own generator (the productions it
just made), and any author in `ignore_authors`. Give the generator an author
nobody else commits as. A target that feeds its own inputs (a chat reply
appended to the messages it reads) is a cycle through the store; the reactor
stops after `max_runs` consecutive runs without settling and says so in
`error`, rather than looping on a model. Name the feeding author in
`ignore_authors`, or put a generational boundary between them (the founder's
"mediate via a strip").

Runs coalesce: changes that arrive while a run is cooking set one flag, and
the run after it covers them all.

Not handled here: settling (IIR) nodes, which need warm-up resurrection
(REFERENCE-MODEL §17); triggering by access-pattern window rather than by
name (an append to a strip outside a reader's window still triggers a run,
and today the reader's cache key covers the whole strip, so it recooks);
scheduling by time (a reactor reacts to changes, not to a clock).
"""

from __future__ import annotations

import asyncio
from typing import Callable, Dict, FrozenSet, Iterable, List, Optional, Set, Tuple

from .generation import Generation, Generator
from .store import Manifest

Key = Tuple[str, str]


class Runaway(RuntimeError):
    """The targets kept changing their own inputs; the reactor stopped."""


def reads_of(store, targets: Iterable[Key]) -> Set[Key]:
    """Every (scope, name) the targets read, transitively through edges,
    including inputs that do not exist yet (so their arrival can be noticed).
    Tolerant of dangling edges and cycles: it collects, it does not judge."""
    reads: Set[Key] = set()
    stack: List[Key] = list(targets)
    while stack:
        key = stack.pop()
        if key in reads:
            continue
        reads.add(key)
        node = store.get(store.current(key[0]), key[1])
        if node is None:
            continue
        for e in node.edges:
            stack.append((e.scope or key[0], e.target))
    return reads


class Reactor:
    def __init__(
        self,
        generator: Generator,
        targets: Iterable[Key],
        *,
        ignore_authors: Iterable[str] = (),
        on_result: Optional[Callable[[Dict[Key, Generation]], None]] = None,
        max_runs: int = 50,
    ) -> None:
        self.generator = generator
        self.store = generator.store
        self.targets: List[Key] = [(sc, nid) for sc, nid in targets]
        self.ignore: FrozenSet[str] = frozenset(ignore_authors) | {generator.author}
        self.on_result = on_result
        self.max_runs = max_runs
        self.runs = 0  # productions of the targets this reactor has run
        self.last: Dict[Key, Generation] = {}
        self.error: Optional[BaseException] = None
        self.reads: Set[Key] = set()
        self._seen: Dict[str, Manifest] = {}
        self._dirty = False
        self._task: Optional["asyncio.Task[None]"] = None
        self._listening = False

    # -- lifecycle -------------------------------------------------------------------

    def start(self) -> "Reactor":
        """Begin listening, and produce the targets once so they start fresh."""
        if not self._listening:
            self._refresh_reads()
            self.store.listeners.append(self._listen)
            self._listening = True
        self._kick()
        return self

    def stop(self) -> None:
        if self._listening:
            self.store.listeners.remove(self._listen)
            self._listening = False

    async def settle(self) -> None:
        """Wait until no change is pending and no run is cooking."""
        while True:
            if self._dirty and (self._task is None or self._task.done()):
                self._task = asyncio.get_running_loop().create_task(self._loop())
            if self._task is None or self._task.done():
                if not self._dirty:
                    return
                continue
            await self._task

    # -- noticing --------------------------------------------------------------------

    def _listen(self, event: str, manifest: Manifest) -> None:
        scope = manifest.scope
        if scope not in self._seen:
            return  # a scope no target reads
        prev = self._seen[scope]
        self._seen[scope] = manifest
        if manifest.author in self.ignore:
            return
        changed = self.store.diff(prev, manifest).keys
        if any((scope, name) in self.reads for name in changed):
            self._kick()

    def _kick(self) -> None:
        self._dirty = True
        try:
            loop = asyncio.get_running_loop()
        except RuntimeError:
            return  # no loop here (a commit from synchronous code); settle() will run it
        if self._task is None or self._task.done():
            self._task = loop.create_task(self._loop())

    def _refresh_reads(self) -> None:
        self.reads = reads_of(self.store, self.targets)
        for scope in {sc for sc, _ in self.reads}:
            if scope not in self._seen:
                self._seen[scope] = self.store.current(scope)

    # -- running ---------------------------------------------------------------------

    async def _loop(self) -> None:
        consecutive = 0
        while self._dirty:
            self._dirty = False
            consecutive += 1
            if consecutive > self.max_runs:
                self.error = Runaway(
                    f"{self.max_runs} consecutive runs without settling: a target is feeding its own inputs "
                    f"(name the feeding author in ignore_authors, or put a generational boundary between them)"
                )
                return
            try:
                result = await self.generator.produce_many(self.targets)
            except Exception as exc:  # a failed run is a state, as a failed production is
                self.error = exc
                self.runs += 1
                self._refresh_reads()
                continue
            self.runs += 1
            self.last = result
            self.error = None
            self._refresh_reads()
            if self.on_result is not None:
                self.on_result(result)


__all__ = ["Reactor", "Runaway", "reads_of"]
