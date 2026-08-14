"""Tests for the content-addressable node-output cache."""

from __future__ import annotations

from pathlib import Path

import pytest

from nodecules.core.node_cache import (
    CacheKey,
    FilesystemNodeCache,
    InMemoryNodeCache,
    build_cache_key,
    hash_input,
    hash_params,
    hash_window,
)
from nodecules.core.time import TimeRange


# --- CacheKey + hashing --------------------------------------------------


class TestHashDeterminism:
    def test_params_hash_is_stable_across_key_order(self) -> None:
        a = hash_params({"x": 1, "y": 2})
        b = hash_params({"y": 2, "x": 1})
        assert a == b

    def test_params_none_and_empty_dict_match(self) -> None:
        assert hash_params(None) == hash_params({})

    def test_input_hash_detects_change(self) -> None:
        assert hash_input({"a": 1}) != hash_input({"a": 2})

    def test_window_hash_none_stays_none(self) -> None:
        assert hash_window(None) is None

    def test_window_hash_changes_with_range(self) -> None:
        w1 = TimeRange(start_ms=0, end_ms=1_000)
        w2 = TimeRange(start_ms=0, end_ms=2_000)
        assert hash_window(w1) != hash_window(w2)


class TestCacheKeyDigest:
    def _key(self, **overrides) -> CacheKey:
        defaults = dict(
            node_type="example",
            node_version="0.1.0",
            params_hash=hash_params({"temperature": 0.5}),
            input_hashes=(hash_input("hello"),),
            window_hash=None,
            annotation_hash=None,
        )
        defaults.update(overrides)
        return CacheKey(**defaults)

    def test_same_inputs_same_digest(self) -> None:
        a = self._key()
        b = self._key()
        assert a.digest() == b.digest()

    def test_different_node_version_different_digest(self) -> None:
        a = self._key(node_version="0.1.0")
        b = self._key(node_version="0.1.1")
        assert a.digest() != b.digest()

    def test_window_hash_disambiguates(self) -> None:
        a = self._key(window_hash=None)
        b = self._key(window_hash=hash_window(TimeRange(start_ms=0, end_ms=1000)))
        assert a.digest() != b.digest()

    def test_annotation_hash_disambiguates(self) -> None:
        a = self._key(annotation_hash=None)
        b = self._key(annotation_hash="abc123")
        assert a.digest() != b.digest()

    def test_static_node_digest_unaffected_by_temporal_fields(self) -> None:
        """Regression guard: the promise in TEMPORALITY.md is that
        existing static-node cache entries stay valid because their
        `window_hash` and `annotation_hash` are both `None`."""
        a = self._key(window_hash=None, annotation_hash=None)
        assert (
            a.digest()
            == self._key(window_hash=None, annotation_hash=None).digest()
        )


class TestBuildCacheKey:
    def test_annotation_none_vs_empty_list_differ(self) -> None:
        """`None` = 'node doesn't read annotations'; `[]` = 'reads but none here'."""
        a = build_cache_key(
            node_type="x",
            node_version="0.1.0",
            annotation_content_hashes=None,
        )
        b = build_cache_key(
            node_type="x",
            node_version="0.1.0",
            annotation_content_hashes=[],
        )
        assert a.digest() != b.digest()

    def test_annotation_order_irrelevant(self) -> None:
        """Ordering of annotation content hashes must not affect the key."""
        a = build_cache_key(
            node_type="x",
            node_version="0.1.0",
            annotation_content_hashes=["h1", "h2", "h3"],
        )
        b = build_cache_key(
            node_type="x",
            node_version="0.1.0",
            annotation_content_hashes=["h3", "h1", "h2"],
        )
        assert a.digest() == b.digest()

    def test_input_order_matters(self) -> None:
        """Port order is part of the node's observable interface — swapping
        two inputs changes the key."""
        a = build_cache_key(
            node_type="x",
            node_version="0.1.0",
            inputs=("alpha", "beta"),
        )
        b = build_cache_key(
            node_type="x",
            node_version="0.1.0",
            inputs=("beta", "alpha"),
        )
        assert a.digest() != b.digest()


# --- InMemoryNodeCache ---------------------------------------------------


class TestInMemoryCache:
    def _key(self) -> CacheKey:
        return build_cache_key(
            node_type="example",
            node_version="0.1.0",
            inputs=("x",),
        )

    def test_miss_returns_none(self) -> None:
        c = InMemoryNodeCache()
        assert c.get(self._key()) is None
        assert not c.has(self._key())

    def test_put_then_get(self) -> None:
        c = InMemoryNodeCache()
        k = self._key()
        c.put(k, {"result": "hello"})
        assert c.has(k)
        assert c.get(k) == {"result": "hello"}

    def test_invalidate(self) -> None:
        c = InMemoryNodeCache()
        k = self._key()
        c.put(k, 42)
        c.invalidate(k)
        assert not c.has(k)

    def test_pydantic_value_round_trips(self) -> None:
        c = InMemoryNodeCache()
        k = self._key()
        w = TimeRange(start_ms=0, end_ms=1_000)
        c.put(k, w)
        # Value stored as dict form; caller re-parses if they want a model.
        assert c.get(k) == {"start_ms": 0, "end_ms": 1_000}


# --- FilesystemNodeCache -------------------------------------------------


class TestFilesystemCache:
    def _key(self, v: str = "hello") -> CacheKey:
        return build_cache_key(
            node_type="example",
            node_version="0.1.0",
            inputs=(v,),
        )

    def test_put_then_get(self, tmp_path: Path) -> None:
        c = FilesystemNodeCache(tmp_path)
        k = self._key()
        c.put(k, {"result": 42})
        assert c.has(k)
        assert c.get(k) == {"result": 42}

    def test_persists_across_instances(self, tmp_path: Path) -> None:
        """The whole point of a filesystem cache — a new process sees old entries."""
        c1 = FilesystemNodeCache(tmp_path)
        k = self._key()
        c1.put(k, "persisted")
        c2 = FilesystemNodeCache(tmp_path)
        assert c2.get(k) == "persisted"

    def test_miss_returns_none(self, tmp_path: Path) -> None:
        c = FilesystemNodeCache(tmp_path)
        assert c.get(self._key("never-stored")) is None
        assert not c.has(self._key("never-stored"))

    def test_invalidate_removes_file(self, tmp_path: Path) -> None:
        c = FilesystemNodeCache(tmp_path)
        k = self._key()
        c.put(k, "x")
        assert c.has(k)
        c.invalidate(k)
        assert not c.has(k)
        # File is actually gone on disk.
        assert not any(tmp_path.glob("*.json"))

    def test_zero_byte_file_treated_as_absent(self, tmp_path: Path) -> None:
        """Aborted writes must not count as cache hits."""
        c = FilesystemNodeCache(tmp_path)
        k = self._key()
        # Fabricate a zero-byte file at the key's path.
        (tmp_path / f"{k.digest()}.json").touch()
        assert not c.has(k)
        assert c.get(k) is None

    def test_corrupt_file_treated_as_absent(self, tmp_path: Path) -> None:
        c = FilesystemNodeCache(tmp_path)
        k = self._key()
        (tmp_path / f"{k.digest()}.json").write_text("{not valid json")
        # has() sees non-empty file → True, but get() returns None on JSON
        # error. The scheduler's "if has(): emit from cache else: run" is
        # safe — a corrupt entry gets a cache miss via get() returning None.
        assert c.get(k) is None

    def test_file_contents_include_key_payload(self, tmp_path: Path) -> None:
        """Audit-friendliness guarantee."""
        import json

        c = FilesystemNodeCache(tmp_path)
        k = self._key()
        c.put(k, "v")
        path = tmp_path / f"{k.digest()}.json"
        blob = json.loads(path.read_text())
        assert blob["value"] == "v"
        assert blob["key"]["node_type"] == "example"
        assert "written_at_iso" in blob


# --- Storage prep --------------------------------------------------------


class TestValuePreparation:
    def test_pydantic_nested_in_dict(self, tmp_path: Path) -> None:
        c = FilesystemNodeCache(tmp_path)
        k = build_cache_key(node_type="x", node_version="0.1.0")
        c.put(
            k,
            {
                "window": TimeRange(start_ms=0, end_ms=1_000),
                "value": 7,
            },
        )
        got = c.get(k)
        assert got == {
            "window": {"start_ms": 0, "end_ms": 1_000},
            "value": 7,
        }

    def test_pydantic_nested_in_list(self, tmp_path: Path) -> None:
        c = FilesystemNodeCache(tmp_path)
        k = build_cache_key(node_type="x", node_version="0.1.0")
        c.put(
            k,
            [TimeRange(start_ms=0, end_ms=10), TimeRange(start_ms=10, end_ms=20)],
        )
        got = c.get(k)
        assert got == [
            {"start_ms": 0, "end_ms": 10},
            {"start_ms": 10, "end_ms": 20},
        ]
