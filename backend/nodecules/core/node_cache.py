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

Two backends ship here:

- `FilesystemNodeCache` — primary. One JSON file per key under a root dir.
  Atomic writes (tempfile + rename). The root dir is supplied by the
  caller — stenota uses `<sidecar>/cache/`; tests use `tmp_path`.
- `InMemoryNodeCache` — dict-backed. Tests and short-lived subprocess caches.

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

    def put(self, key: CacheKey, value: Any) -> None: ...

    def invalidate(self, key: CacheKey) -> None: ...


class InMemoryNodeCache:
    """Dict-backed cache. No persistence. Tests + ephemeral executions."""

    def __init__(self) -> None:
        self._store: dict[str, Any] = {}

    def has(self, key: CacheKey) -> bool:
        return key.digest() in self._store

    def get(self, key: CacheKey) -> Optional[Any]:
        return self._store.get(key.digest())

    def put(self, key: CacheKey, value: Any) -> None:
        self._store[key.digest()] = _prepare_for_storage(value)

    def invalidate(self, key: CacheKey) -> None:
        self._store.pop(key.digest(), None)

    def __len__(self) -> int:
        return len(self._store)


class FilesystemNodeCache:
    """Filesystem-backed cache. One JSON file per key under `root`.

    File contents:
      `{"key": <CacheKey payload>, "value": <stored value>, "written_at_iso": "..."}`

    Storing the key next to the value makes cache directories diffable and
    audit-friendly: you can grep a sidecar for "what did node X do at
    window W?" by inspecting the files directly.
    """

    def __init__(self, root: str | os.PathLike[str]) -> None:
        self._root = Path(root)
        self._root.mkdir(parents=True, exist_ok=True)

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

    def put(self, key: CacheKey, value: Any) -> None:
        p = self._path_for(key)
        _atomic_write_text(
            p,
            _canonical_json(
                {
                    "key": _cache_key_payload(key),
                    "value": _prepare_for_storage(value),
                    "written_at_iso": _now_iso(),
                }
            ),
        )

    def invalidate(self, key: CacheKey) -> None:
        try:
            self._path_for(key).unlink()
        except FileNotFoundError:
            pass


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
