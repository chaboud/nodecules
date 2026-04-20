"""Execution event log — the data feed behind the observability timeline.

Every decision the scheduler makes (node started, node completed, cache
hit, window emitted, annotation landed, graph closed) emits a tiny
`ExecutionEvent`. Sinks persist them — a JSONL file for real runs, an
in-memory list for tests, a no-op for when nothing cares.

The schema is intentionally small and stable. It's the input to any
future viewer (the cinnamon-roll timeline in the frontend, a CLI
`nodecules trace`, a Tauri debugger). Locking the schema before UIs
exist means runs today produce data tomorrow's UIs can read.
"""

from __future__ import annotations

import json
import os
import tempfile
from abc import abstractmethod
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, List, Literal, Optional, Protocol
from uuid import uuid4

from pydantic import BaseModel, ConfigDict, Field

from .time import TimeRange


EventKind = Literal[
    "graph_start",
    "graph_close",
    "node_start",
    "node_complete",
    "node_failed",
    "cache_hit",
    "window_emit",
    "annotation_added",
    "annotation_removed",
]


class ExecutionEvent(BaseModel):
    """One line in the execution log. Frozen, JSON-roundtripping, tiny.

    Fields are optional unless essential to the kind. `meta` is the
    escape hatch for kind-specific payload that doesn't warrant a
    first-class field. Keep `meta` small — this schema is meant for
    streaming append, not blob storage.
    """

    model_config = ConfigDict(frozen=True)

    event_id: str = Field(default_factory=lambda: str(uuid4()))
    kind: EventKind
    # Wall-clock of emission, ISO 8601 with tz. Useful for ordering
    # cross-process events that share a filesystem.
    wall_ts_iso: str = Field(
        default_factory=lambda: datetime.now(timezone.utc).isoformat(timespec="microseconds")
    )
    # Meeting-relative ms from the scheduler's TimeSource. Null for
    # events that aren't anchored to meeting time (graph_start /
    # graph_close).
    meeting_ts_ms: Optional[int] = None

    graph_id: Optional[str] = None
    execution_id: Optional[str] = None

    # Node-scoped fields — populated only when kind is node_*.
    node_id: Optional[str] = None
    node_type: Optional[str] = None
    node_version: Optional[str] = None

    # Window-scoped fields.
    window: Optional[TimeRange] = None
    cache_key_digest: Optional[str] = None

    # Timing + error fields.
    latency_ms: Optional[int] = None
    error: Optional[str] = None

    # Escape hatch for kind-specific payload. Keep it small.
    meta: dict[str, Any] = Field(default_factory=dict)


# --- Sink protocol + implementations -------------------------------------


class EventSink(Protocol):
    """Implementations receive events synchronously on the scheduler thread."""

    def emit(self, event: ExecutionEvent) -> None: ...

    def close(self) -> None: ...


class NullEventSink:
    """Drops everything. Default when observability is off."""

    def emit(self, event: ExecutionEvent) -> None:
        return

    def close(self) -> None:
        return


class ListEventSink:
    """Collects events in memory. Tests; small jobs; fast iteration."""

    def __init__(self) -> None:
        self.events: List[ExecutionEvent] = []

    def emit(self, event: ExecutionEvent) -> None:
        self.events.append(event)

    def close(self) -> None:
        return

    # --- Test conveniences (not part of the EventSink protocol) ----------

    def of_kind(self, kind: EventKind) -> List[ExecutionEvent]:
        return [e for e in self.events if e.kind == kind]

    def __len__(self) -> int:
        return len(self.events)


class JsonlEventSink:
    """Appends one JSONL record per event to a file.

    Buffered writes — `emit()` does NOT fsync per-event (would be fatal
    for high-emission graphs). `close()` flushes + fsyncs. For
    crash-durability of events in long batch runs, call `close()` at
    `graph_close`; the scheduler does this.
    """

    def __init__(self, path: str | os.PathLike[str]) -> None:
        self._path = Path(path)
        self._path.parent.mkdir(parents=True, exist_ok=True)
        self._fh = open(self._path, "a", encoding="utf-8")

    def emit(self, event: ExecutionEvent) -> None:
        self._fh.write(event.model_dump_json())
        self._fh.write("\n")

    def close(self) -> None:
        try:
            self._fh.flush()
            os.fsync(self._fh.fileno())
        finally:
            self._fh.close()


def read_events(path: str | os.PathLike[str]) -> List[ExecutionEvent]:
    """Parse a JSONL event log back into `ExecutionEvent` objects.

    Strict — a malformed line raises, because an event log that
    partially-parses is more dangerous than one that fails loudly.
    """
    out: List[ExecutionEvent] = []
    with open(path, encoding="utf-8") as f:
        for line_no, raw in enumerate(f, start=1):
            line = raw.strip()
            if not line:
                continue
            try:
                out.append(ExecutionEvent.model_validate_json(line))
            except Exception as exc:
                raise ValueError(
                    f"{path}:{line_no} — could not parse ExecutionEvent: {exc}"
                ) from exc
    return out


__all__ = [
    "EventKind",
    "EventSink",
    "ExecutionEvent",
    "JsonlEventSink",
    "ListEventSink",
    "NullEventSink",
    "read_events",
]
