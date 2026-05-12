"""Strip API — named lazy views over sidecar JSONL files.

A strip is a named, time-indexed publication. Consumers depend on a strip
by name (not by node ID), which keeps producer-swappability clean: the
processor that produces `strips/turns/diarized` can be swapped without
touching any consumer of that strip.

Strips are registered with a `StripRegistry` that knows the sidecar-
relative path of the backing file, the Pydantic-shaped class to
deserialize lines as, and an optional filter (e.g., `kind == "turn"`).
Library users (stenota) register strips at import time; nodes consume by
name via `ChunkedContext.strip(name)`.

**Duck-typing on purpose.** Anything with a `model_validate_json`
classmethod and a `time_range`-shaped attribute satisfies the strip
schema contract. Stenota's `stenota.core.models.StructuredClaim` matches
without any modification; nodecules never imports stenota's models.

The pull API on a strip:

- `iter(strip)` — every event in arrival order, lazy.
- `strip[i]` / `strip[i:j]` — ordinal indexing. Materializes.
- `strip.at(i)` — bounds-tolerant ordinal lookup; returns None if out of range.
- `strip.in_range(tr)` — lazy iterator over events whose time-range
  intersects `tr`.
- `strip.before(tr)` — most recent event ending strictly before `tr.start_ms`.
- `strip.after(tr)` — first event starting at or after `tr.end_ms`.
- `strip.latest()` — most recent event.

A push API counterpart lands in PR-n6.

Cycle prevention is deferred to PR-n7's graph-load validator; for now
the API forbids forward indexing only at runtime (an out-of-bounds
`strip[me + k]` raises naturally via list indexing).
"""

from __future__ import annotations

import os
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Callable, Iterator, Literal, Optional, Protocol, runtime_checkable

from .time import TimeRange


IndexKind = Literal["time_range", "ordinal"]


@runtime_checkable
class StripSchema(Protocol):
    """Duck-typed schema protocol.

    Anything with a `model_validate_json` classmethod satisfies this. Pydantic
    BaseModel subclasses match without modification. Non-Pydantic schemas can
    be adapted with a thin wrapper.
    """

    @classmethod
    def model_validate_json(cls, raw: str) -> "StripSchema":
        ...


@dataclass(frozen=True)
class StripSpec:
    """Declarative spec for one strip.

    `name` is hierarchical by convention (`strips/turns/diarized`). The
    registry treats it as an opaque string; convention discipline is the
    library user's responsibility.

    `relative_path` is sidecar-relative — e.g., `claims/L2.jsonl`. The
    file may or may not exist at registration time; iteration handles
    missing files as empty.

    `schema_cls` is duck-typed: any class with `model_validate_json` works.

    `filter_fn` is applied lazily during iteration so a single backing
    file can host multiple strips (turns + summarizer-emitted claims both
    live in `claims/L2.jsonl` filtered by `kind`).

    `index_kind` is metadata for callers; the API doesn't enforce it.

    Equality is structural — two specs with the same name, path, schema,
    and (filter_fn identity, index_kind) match. Re-registering an identical
    spec is a no-op.
    """

    name: str
    relative_path: str
    schema_cls: type[StripSchema]
    filter_fn: Optional[Callable[[Any], bool]] = None
    index_kind: IndexKind = "time_range"
    description: str = ""


class StripRegistry:
    """Process-wide registry mapping strip names to specs.

    Stenota registers its strips at import time:

        REGISTRY.register(StripSpec(
            name="strips/turns/diarized",
            relative_path="claims/L2.jsonl",
            schema_cls=StructuredClaim,
            filter_fn=lambda c: c.kind == "turn",
        ))

    Registration is idempotent for identical specs; mismatched specs under
    the same name raise. This protects against accidental shadowing when
    two import paths both try to register the same strip.
    """

    def __init__(self) -> None:
        self._specs: dict[str, StripSpec] = {}

    def register(self, spec: StripSpec) -> None:
        existing = self._specs.get(spec.name)
        if existing is not None and existing != spec:
            raise ValueError(
                f"strip {spec.name!r} already registered with a different spec; "
                "unregister or use a distinct name"
            )
        self._specs[spec.name] = spec

    def unregister(self, name: str) -> None:
        """Remove a registration. Tests use this to keep state clean."""
        self._specs.pop(name, None)

    def get(self, name: str) -> StripSpec:
        try:
            return self._specs[name]
        except KeyError:
            raise KeyError(
                f"no registered strip named {name!r}; "
                f"registered: {sorted(self._specs)}"
            )

    def has(self, name: str) -> bool:
        return name in self._specs

    def list_names(self) -> list[str]:
        return sorted(self._specs)

    def clear(self) -> None:
        """Drop every registration. Tests use this between cases."""
        self._specs.clear()


class StripView:
    """Lazy iterator + indexer over one strip in one sidecar.

    Construct via `StripRegistry.get(name)` + a sidecar path. Reads happen
    on access, not on construction. Each iteration re-reads the JSONL file
    — appropriate for the append-on-line pattern where the file is
    growing during a live run.

    Ordinal indexing materializes the strip. For long strips, prefer the
    time-range queries (`in_range`, `before`, `after`).
    """

    def __init__(self, spec: StripSpec, sidecar: str | os.PathLike[str]) -> None:
        self._spec = spec
        self._path = Path(sidecar) / spec.relative_path

    # --- Identity --------------------------------------------------------

    @property
    def name(self) -> str:
        return self._spec.name

    @property
    def index_kind(self) -> IndexKind:
        return self._spec.index_kind

    @property
    def path(self) -> Path:
        return self._path

    # --- Iteration -------------------------------------------------------

    def __iter__(self) -> Iterator[Any]:
        """Yield every event passing the optional filter, in arrival order.

        Missing or empty backing files yield nothing rather than raising.
        Malformed JSONL lines raise immediately — silent-skip is more
        dangerous than failing loudly when the schema's evolving.
        """
        if not self._path.exists():
            return
        try:
            fh = open(self._path, encoding="utf-8")
        except FileNotFoundError:
            return
        with fh:
            for line_no, raw in enumerate(fh, start=1):
                line = raw.strip()
                if not line:
                    continue
                try:
                    obj = self._spec.schema_cls.model_validate_json(line)
                except Exception as exc:
                    raise ValueError(
                        f"{self._path}:{line_no} — could not parse as "
                        f"{self._spec.schema_cls.__name__}: {exc}"
                    ) from exc
                if self._spec.filter_fn is not None and not self._spec.filter_fn(obj):
                    continue
                yield obj

    # --- Ordinal indexing ------------------------------------------------

    def __getitem__(self, key: int | slice) -> Any:
        """Materialize then index. Use sparingly on long strips."""
        items = list(self)
        return items[key]

    def at(self, index: int) -> Optional[Any]:
        """Bounds-tolerant counterpart to `__getitem__`.

        Returns None if `index` is out of range. Used by nodes that want
        `strip.at(me - 1)` without raising at meeting start.
        """
        try:
            return self[index]
        except IndexError:
            return None

    def __len__(self) -> int:
        return sum(1 for _ in self)

    def latest(self) -> Optional[Any]:
        """Most recent event, or None if the strip is empty.

        Iterates the file; for long strips, prefer holding the latest via a
        subscription (PR-n6).
        """
        last: Optional[Any] = None
        for evt in self:
            last = evt
        return last

    # --- Time-range queries ---------------------------------------------

    def in_range(self, time_range: TimeRange) -> Iterator[Any]:
        """Yield events whose time_range intersects `time_range`.

        Time-range matching uses the event's `time_range` attribute (raw
        evidence convention) or `source_window` (claims convention).
        Events with neither are skipped.
        """
        for evt in self:
            evt_range = _event_time_range(evt)
            if evt_range is None:
                continue
            if evt_range.intersects(time_range):
                yield evt

    def before(self, time_range: TimeRange) -> Optional[Any]:
        """Most recent event ending strictly before `time_range.start_ms`.

        Ordering assumes arrival order ≈ time order — stenota's
        append-on-emit pattern satisfies this. Misordered files produce
        approximate-but-defensible results.
        """
        candidate: Optional[Any] = None
        for evt in self:
            evt_range = _event_time_range(evt)
            if evt_range is None:
                continue
            if evt_range.end_ms <= time_range.start_ms:
                candidate = evt
            elif evt_range.start_ms >= time_range.start_ms:
                # We've passed the window of interest; no later event can
                # be the "most recent before" answer.
                break
        return candidate

    def after(self, time_range: TimeRange) -> Optional[Any]:
        """First event starting at or after `time_range.end_ms`."""
        for evt in self:
            evt_range = _event_time_range(evt)
            if evt_range is None:
                continue
            if evt_range.start_ms >= time_range.end_ms:
                return evt
        return None


def _event_time_range(evt: Any) -> Optional[TimeRange]:
    """Pull a TimeRange off an event.

    Tries `time_range` first (raw evidence convention), then `source_window`
    (claims convention). Converts foreign TimeRange shapes (e.g.,
    stenota.core.models.TimeRange) into nodecules' TimeRange by reading
    `start_ms` / `end_ms` attributes — the two types are intentionally
    structurally identical, just declared in different modules.
    """
    rng = getattr(evt, "time_range", None)
    if rng is None:
        rng = getattr(evt, "source_window", None)
    if rng is None:
        return None
    if isinstance(rng, TimeRange):
        return rng
    start = getattr(rng, "start_ms", None)
    end = getattr(rng, "end_ms", None)
    if start is None or end is None:
        return None
    return TimeRange(start_ms=int(start), end_ms=int(end))


__all__ = [
    "IndexKind",
    "StripRegistry",
    "StripSchema",
    "StripSpec",
    "StripView",
]
