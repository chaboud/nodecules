"""The disk tier: a directory as a `Backing` for the store.

Content-addressed, append-only, and plain: one JSON file per body, per
skeleton, and per manifest, named by hash, plus one small file per scope
naming its head. Writes are atomic (write beside, then rename). Reads
verify: a body that does not hash to its filename, or a manifest whose
entries do not rebuild to its recorded root, is refused as corrupt rather
than served. Because the persistent map's shape depends only on its keys,
a manifest's root hash reproduces exactly from its entry list, which is
what makes the second check possible.

Layout under the directory:

    bodies/<hash>.json      a node: id, kind, scope, data, edges
    skeletons/<hash>.json   kind and edges only — enough to answer lineage
                            and composed hashes when the body is released
    manifests/<hash>.json   a manifest's fields plus its [name, hash] entries
    heads/<scope>.json      {"scope": ..., "head": <manifest hash>}

`load(path)` returns a store with manifests and heads in memory and bodies
on disk until read (sparse load, §12). Attaching a backing to a store that
already has content writes that content through first.
"""

from __future__ import annotations

import json
import os
from pathlib import Path
from typing import Dict, Iterator, Optional, Tuple
from urllib.parse import quote, unquote

from .pmap import PMap
from .store import Backing, Edge, Manifest, Node, Store


class Corrupt(Exception):
    """A file on disk does not hash to what its name or record claims."""


def _write_atomic(path: Path, payload: dict) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(path.suffix + ".tmp")
    with tmp.open("w", encoding="utf-8") as f:
        json.dump(payload, f, sort_keys=True, separators=(",", ":"))
        f.flush()
        os.fsync(f.fileno())
    os.replace(tmp, path)


def _read(path: Path) -> Optional[dict]:
    if not path.exists():
        return None
    with path.open("r", encoding="utf-8") as f:
        return json.load(f)


class DiskBacking(Backing):
    def __init__(self, root: str | Path) -> None:
        self.root = Path(root)
        for sub in ("bodies", "skeletons", "manifests", "heads"):
            (self.root / sub).mkdir(parents=True, exist_ok=True)

    # -- bodies ----------------------------------------------------------------------

    def _body_path(self, h: str) -> Path:
        return self.root / "bodies" / f"{h}.json"

    def put_body(self, node: Node) -> None:
        h = node.content_hash()
        p = self._body_path(h)
        if not p.exists():
            _write_atomic(p, node.model_dump(mode="json"))
        sk = self.root / "skeletons" / f"{h}.json"
        if not sk.exists():
            _write_atomic(sk, {"kind": node.kind, "edges": [e.model_dump(mode="json") for e in node.edges]})

    def get_body(self, content_hash: str) -> Optional[Node]:
        raw = _read(self._body_path(content_hash))
        if raw is None:
            return None
        node = Node(**raw)
        if node.content_hash() != content_hash:
            raise Corrupt(f"body {content_hash[:12]} does not hash to its name")
        return node

    def has_body(self, content_hash: str) -> bool:
        return self._body_path(content_hash).exists()

    def delete_body(self, content_hash: str) -> None:
        p = self._body_path(content_hash)
        if p.exists():
            p.unlink()

    def get_skeleton(self, content_hash: str) -> Optional[Tuple[str, Tuple[Edge, ...]]]:
        raw = _read(self.root / "skeletons" / f"{content_hash}.json")
        if raw is None:
            return None
        return raw["kind"], tuple(Edge(**e) for e in raw["edges"])

    # -- manifests -------------------------------------------------------------------

    def _manifest_path(self, h: str) -> Path:
        return self.root / "manifests" / f"{h}.json"

    def put_manifest(self, manifest: Manifest) -> None:
        h = manifest.content_hash()
        p = self._manifest_path(h)
        if p.exists():
            return
        payload = manifest.model_dump(mode="json")
        payload["entries"] = [[name, body] for name, body in manifest.entries()]
        _write_atomic(p, payload)

    def _manifest_from(self, raw: dict, expected_hash: Optional[str]) -> Manifest:
        entries = PMap.of({name: body for name, body in raw.pop("entries")})
        stored_root = raw.pop("root")
        if entries.root_hash != stored_root:
            raise Corrupt(f"manifest {(expected_hash or '?')[:12]}: entries do not rebuild to the recorded root")
        m = Manifest.build(
            raw["scope"],
            entries,
            seq=raw["seq"],
            parent=raw.get("parent"),
            note=raw.get("note", ""),
            author=raw.get("author", ""),
            rebased_from=raw.get("rebased_from"),
            merge_parent=raw.get("merge_parent"),
            activated=raw.get("activated"),
            resolution=raw.get("resolution", "single-authority"),
            resolution_version=raw.get("resolution_version", 1),
            overrode=tuple(tuple(o) for o in raw.get("overrode", [])),
        )
        if expected_hash is not None and m.content_hash() != expected_hash:
            raise Corrupt(f"manifest {expected_hash[:12]} does not hash to its name")
        return m

    def get_manifest(self, content_hash: str) -> Optional[Manifest]:
        raw = _read(self._manifest_path(content_hash))
        if raw is None:
            return None
        return self._manifest_from(raw, content_hash)

    def manifests(self) -> Iterator[Manifest]:
        for p in sorted((self.root / "manifests").glob("*.json")):
            raw = _read(p)
            if raw is not None:
                yield self._manifest_from(raw, p.stem)

    # -- heads -----------------------------------------------------------------------

    def _head_path(self, scope: str) -> Path:
        return self.root / "heads" / f"{quote(scope, safe='')}.json"

    def set_head(self, scope: str, content_hash: str) -> None:
        _write_atomic(self._head_path(scope), {"scope": scope, "head": content_hash})

    def heads(self) -> Dict[str, str]:
        out: Dict[str, str] = {}
        for p in sorted((self.root / "heads").glob("*.json")):
            raw = _read(p)
            if raw is not None:
                out[unquote(p.stem)] = raw["head"]
        return out


def load(path: str | Path) -> Store:
    """A store over the directory: manifests and heads in memory, bodies on
    disk until read."""
    store = Store()
    store.attach(DiskBacking(path))
    return store


__all__ = ["Corrupt", "DiskBacking", "load"]
