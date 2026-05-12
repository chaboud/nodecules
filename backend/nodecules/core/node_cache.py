"""Content-addressable node-output cache.

Every output of a node is keyed by `(node_type, node_version, params_hash,
input_hashes, window_hash, annotation_hash)`. The first three capture "what
code + what knobs." The fourth captures "what did it read." The last two
isolate temporal slices so a change at window `W` doesn't invalidate windows
that aren't at `W` (and so annotations that touch only `W` only re-run the
subgraph at `W`).

Static-node cache keys set `window_hash` and `annotation_hash` to `None` —
their inclusion in the digest is a canonical JSON `null`, so static-node
keys stay stable as long as `core/types.py` hasn't drifted.

**PR-n4: determinism + eviction.** Cache entries carry an `is_deterministic`
flag (default True). LRU eviction (size or entry-count cap) targets
deterministic entries only — nondeterministic entries (LLM outputs etc.)
are pinned because re-running won't reproduce them. Eviction policies are
opt-in via `FilesystemNodeCache(root, max_size_bytes=..., max_entries=...)`;
when unset, cache grows without bound (current behavior).

Two backends ship here:

- `FilesystemNodeCache` — primary. One JSON file per key under a root dir.
  Atomic writes (tempfile + rename). The root dir is supplied by the
  caller — stenota uses `<sidecar>/cache/`; tests use `tmp_path`.
- `InMemoryNodeCache` — dict-backed (OrderedDict for LRU). Tests and
  short-lived subprocess caches.

No Redis/Postgres backend at this layer. The existing chat-context Redis
cache in `core/content_addressable_context.py` is a different concern (chat
message histories) and lives behind the FastAPI layer per CLAUDE.md
invariant #4.
"""

from __future__ import annotations

import hashlib
import json
import os
import tempfile
from collections import OrderedDict
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Iterable, Optional, Protocol

from pydantic import BaseModel

from .time import TimeRange


# --- Canonical hashing helpers -------------------------------------------


def _canonical_json(obj: Any) -> str:
    """Deterministic JSON for hashing — sorted keys, no whitespace."""
    if isinstance(obj, BaseModel):
        payload = obj.model_dump()
    else:
        payload = obj
    return json.dumps(payload, sort_keys=True, separators=(",", ":"))


def _sha256_hex(data: str) -> str:
    return hashlib.sha256(data.encode("utf-8")).hexdigest()


def hash_params(params: dict[str, Any] | None) -> str:
    """Hash a params dict canonically. `None` maps to the empty-dict hash."""
    return _sha256_hex(_canonical_json(params or {}))


def hash_input(value: Any) -> str:
    """Hash a single input value. Accepts dict / list / primitive / Pydantic."""
    return _sha256_hex(_canonical_json(value))


def hash_window(window: Optional[TimeRange]) -> Optional[str]:
    """Hash a `TimeRange`. Returns `None` for static (no-window) nodes."""
    if window is None:
        return None
    return _sha256_hex(_canonical_json(window))


# --- CacheKey -------------------------------------------------------------


@dataclass(frozen=True)
class CacheKey:
    """Structured cache key. Callers build one; the backend hashes it.

    `input_hashes` is a tuple (ordered; the order is the order of the
    declared input ports on the node spec). Changing port order is
    therefore a cache-invalidating event — which is correct, since port
    order is part of the node's observable interface.

    `window_hash` and `annotation_hash` are `None` for static nodes. The
    resulting digest stays stable as long as `node_type`/`node_version`/
    `params_hash`/`input_hashes` stay the same, which is the pre-
    temporality behavior.
    """

    node_type: str
    node_version: str
    params_hash: str
    input_hashes: tuple[str, ...]
    window_hash: Optional[str] = None
    annotation_hash: Optional[str] = None

    def digest(self) -> str:
        """Short-ish stable hex digest of the whole key."""
        payload = {
            "node_type": self.node_type,
            "node_version": self.node_version,
            "params_hash": self.params_hash,
            "input_hashes": list(self.input_hashes),
            "window_hash": self.window_hash,
            "annotation_hash": self.annotation_hash,
        }
        return _sha256_hex(_canonical_json(payload))


def build_cache_key(
    *,
    node_type: str,
    node_version: str,
    params: dict[str, Any] | None = None,
    inputs: Iterable[Any] = (),
    window: Optional[TimeRange] = None,
    annotation_content_hashes: Optional[Iterable[str]] = None,
) -> CacheKey:
    """Convenience constructor — hashes the raw bits into a `CacheKey`.

    `annotation_content_hashes`: pass the content hashes of the annotations
    whose `target_window` intersects `window`. Order is normalized (sorted)
    so two identical annotation sets hash the same regardless of arrival
    order. Pass `None` for nodes that do not read annotations at all — that
    produces `annotation_hash=None`, which matches static cache keys. Pass
    an empty iterable only for nodes that DO read annotations but happen to
    have none in this window (the hash differs from `None`, preventing a
    "no annotations" state from colliding with a "doesn't care" state).
    """
    if annotation_content_hashes is None:
        ann_hash: Optional[str] = None
    else:
        ordered = sorted(annotation_content_hashes)
        ann_hash = _sha256_hex(_canonical_json(ordered))
    return CacheKey(
        node_type=node_type,
        node_version=node_version,
        params_hash=hash_params(params),
        input_hashes=tuple(hash_input(v) for v in inputs),
        window_hash=hash_window(window),
        annotation_hash=ann_hash,
    )


# --- Backend protocol + implementations ----------------------------------


class NodeCache(Protocol):
    """Interface every cache backend satisfies."""

    def has(self, key: CacheKey) -> bool: ...

    def get(self, key: CacheKey) -> Optional[Any]: ...

    def put(self, key: CacheKey, value: Any, *, is_deterministic: bool = True) -> None: ...

    def invalidate(self, key: CacheKey) -> None: ...


class InMemoryNodeCache:
    """Dict-backed cache. No persistence. Tests + ephemeral executions.

    Internal store is an `OrderedDict` keyed by the cache-key digest;
    `get()` moves the entry to the end (LRU bump). Eviction (when
    `max_entries` is set) removes oldest deterministic entry first;
    nondeterministic entries are pinned and never auto-evicted.
    """

    def __init__(self, *, max_entries: Optional[int] = None) -> None:
        # Each entry is {"value": <stored>, "is_deterministic": bool}.
        self._store: "OrderedDict[str, dict]" = OrderedDict()
        self._max_entries = max_entries

    def has(self, key: CacheKey) -> bool:
        return key.digest() in self._store

    def get(self, key: CacheKey) -> Optional[Any]:
        digest = key.digest()
        entry = self._store.get(digest)
        if entry is None:
            return None
        # LRU bump on read.
        self._store.move_to_end(digest)
        return entry["value"]

    def put(
        self,
        key: CacheKey,
        value: Any,
        *,
        is_deterministic: bool = True,
    ) -> None:
        digest = key.digest()
        self._store[digest] = {
            "value": _prepare_for_storage(value),
            "is_deterministic": bool(is_deterministic),
        }
        self._store.move_to_end(digest)
        self._evict_if_needed()

    def invalidate(self, key: CacheKey) -> None:
        self._store.pop(key.digest(), None)

    def __len__(self) -> int:
        return len(self._store)

    def _evict_if_needed(self) -> None:
        if self._max_entries is None:
            return
        while len(self._store) > self._max_entries:
            # Find the oldest *deterministic* entry. Nondeterministic
            # entries are pinned — they represent recorded LLM / stochastic
            # outputs that can't be reproduced, so we never auto-drop them.
            evicted = False
            for digest in list(self._store.keys()):
                if self._store[digest]["is_deterministic"]:
                    del self._store[digest]
                    evicted = True
                    break
            if not evicted:
                # Every entry is nondeterministic and pinned; the cache
                # exceeds its cap but can't shrink. Caller's responsibility
                # to invalidate explicitly.
                break


class FilesystemNodeCache:
    """Filesystem-backed cache. One JSON file per key under `root`.

    File contents:
      `{"key": <CacheKey payload>, "value": <stored value>,
        "is_deterministic": bool, "written_at_iso": "..."}`

    Storing the key next to the value makes cache directories diffable and
    audit-friendly: you can grep a sidecar for "what did node X do at
    window W?" by inspecting the files directly.

    **Eviction (PR-n4).** When `max_size_bytes` or `max_entries` is set,
    `put()` triggers an LRU sweep that removes oldest deterministic
    entries (by file mtime) until the cap is satisfied. Nondeterministic
    entries are pinned — a recorded LLM output is irreplaceable, so we
    don't auto-drop it under cache pressure. If every entry is
    nondeterministic and the cache still exceeds its cap, eviction
    bottoms out and the cap is exceeded; this is the safe failure mode.
    """

    def __init__(
        self,
        root: str | os.PathLike[str],
        *,
        max_size_bytes: Optional[int] = None,
        max_entries: Optional[int] = None,
    ) -> None:
        self._root = Path(root)
        self._root.mkdir(parents=True, exist_ok=True)
        self._max_size_bytes = max_size_bytes
        self._max_entries = max_entries

    def _path_for(self, key: CacheKey) -> Path:
        return self._root / f"{key.digest()}.json"

    def has(self, key: CacheKey) -> bool:
        p = self._path_for(key)
        try:
            return p.stat().st_size > 0
        except FileNotFoundError:
            return False

    def get(self, key: CacheKey) -> Optional[Any]:
        p = self._path_for(key)
        if not p.is_file():
            return None
        try:
            blob = json.loads(p.read_text(encoding="utf-8"))
        except json.JSONDecodeError:
            # Corrupted cache entry (partial write from an old crash, etc.)
            # — treat as absent. The scheduler will re-run the node.
            return None
        return blob.get("value")

    def put(
        self,
        key: CacheKey,
        value: Any,
        *,
        is_deterministic: bool = True,
    ) -> None:
        p = self._path_for(key)
        _atomic_write_text(
            p,
            _canonical_json(
                {
                    "key": _cache_key_payload(key),
                    "value": _prepare_for_storage(value),
                    "is_deterministic": bool(is_deterministic),
                    "written_at_iso": _now_iso(),
                }
            ),
        )
        if self._max_size_bytes is not None or self._max_entries is not None:
            self._evict_if_needed()

    def invalidate(self, key: CacheKey) -> None:
        try:
            self._path_for(key).unlink()
        except FileNotFoundError:
            pass

    # --- Eviction internals --------------------------------------------

    def _evict_if_needed(self) -> None:
        """Sweep oldest-first; remove deterministic entries while cap exceeded.

        Determinism is read from each entry's `is_deterministic` field.
        Missing field (legacy entries from pre-PR-n4 caches) defaults to
        True — evictable. Unparseable entries are also treated as
        evictable; they're corrupted anyway.
        """
        entries = self._scan_entries()
        if not entries:
            return
        # entries is oldest-first (lowest mtime first)
        if self._max_entries is not None:
            while len(entries) > self._max_entries:
                idx = _index_of_evictable(entries)
                if idx is None:
                    break  # every remaining entry is pinned
                entries.pop(idx)[0].unlink(missing_ok=True)
        if self._max_size_bytes is not None:
            total = sum(st.st_size for _, st, _ in entries)
            while total > self._max_size_bytes:
                idx = _index_of_evictable(entries)
                if idx is None:
                    break
                path, st, _ = entries.pop(idx)
                path.unlink(missing_ok=True)
                total -= st.st_size

    def _scan_entries(self) -> list[tuple[Path, os.stat_result, bool]]:
        """Return all cache entries as `(path, stat, is_deterministic)`,
        sorted oldest-first by mtime."""
        rows: list[tuple[Path, os.stat_result, bool]] = []
        for p in self._root.glob("*.json"):
            if not p.is_file():
                continue
            try:
                st = p.stat()
            except FileNotFoundError:
                continue
            try:
                blob = json.loads(p.read_text(encoding="utf-8"))
                is_det = bool(blob.get("is_deterministic", True))
            except Exception:
                # Corrupted entry; treat as evictable so the eviction
                # path can clean it up.
                is_det = True
            rows.append((p, st, is_det))
        rows.sort(key=lambda row: row[1].st_mtime)
        return rows


# --- Internal helpers ----------------------------------------------------


def _cache_key_payload(key: CacheKey) -> dict[str, Any]:
    return {
        "node_type": key.node_type,
        "node_version": key.node_version,
        "params_hash": key.params_hash,
        "input_hashes": list(key.input_hashes),
        "window_hash": key.window_hash,
        "annotation_hash": key.annotation_hash,
    }


def _prepare_for_storage(value: Any) -> Any:
    """Turn a stored value into something JSON-serializable.

    Pydantic models get `.model_dump()`'d. Other values pass through — if
    they're not already JSON-serializable, `json.dumps` will raise at write
    time, which is the signal we want (caller wrote something the cache
    can't round-trip).
    """
    if isinstance(value, BaseModel):
        return value.model_dump()
    if isinstance(value, list):
        return [_prepare_for_storage(x) for x in value]
    if isinstance(value, dict):
        return {k: _prepare_for_storage(v) for k, v in value.items()}
    return value


def _index_of_evictable(
    entries: list[tuple[Path, os.stat_result, bool]],
) -> Optional[int]:
    """Return the index of the oldest evictable (is_deterministic=True) entry.

    Returns None if every remaining entry is pinned (nondeterministic).
    Used by the eviction loop so we don't drop irreplaceable LLM outputs.
    """
    for i, (_, _, is_det) in enumerate(entries):
        if is_det:
            return i
    return None


def _atomic_write_text(path: Path, text: str) -> None:
    """tempfile + rename in the same dir — survives crashes mid-write."""
    path.parent.mkdir(parents=True, exist_ok=True)
    fd, tmp = tempfile.mkstemp(
        prefix=path.name + ".", suffix=".tmp", dir=str(path.parent)
    )
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as f:
            f.write(text)
            f.flush()
            os.fsync(f.fileno())
        os.replace(tmp, path)
    except Exception:
        try:
            os.unlink(tmp)
        except FileNotFoundError:
            pass
        raise


def _now_iso() -> str:
    from datetime import datetime, timezone

    return datetime.now(timezone.utc).isoformat(timespec="seconds")


__all__ = [
    "CacheKey",
    "FilesystemNodeCache",
    "InMemoryNodeCache",
    "NodeCache",
    "build_cache_key",
    "hash_input",
    "hash_params",
    "hash_window",
]
