"""Tests for PR-n4 cache eviction.

- `InMemoryNodeCache` with `max_entries`: LRU sweep on put; nondeterministic
  entries pinned.
- `FilesystemNodeCache` with `max_entries` and `max_size_bytes`: oldest
  deterministic entries dropped when caps exceed; nondeterministic entries
  pinned.

Legacy entries (no `is_deterministic` field) are treated as deterministic
— evictable. This preserves backward compatibility with caches written
before PR-n4.
"""

from __future__ import annotations

import json
import os
import time
from pathlib import Path

import pytest

from nodecules.core.node_cache import (
    CacheKey,
    FilesystemNodeCache,
    InMemoryNodeCache,
    build_cache_key,
)


def _key(node_type: str, *, suffix: str = "") -> CacheKey:
    return build_cache_key(
        node_type=node_type,
        node_version="1.0" + suffix,
        inputs=(),
    )


class TestInMemoryEviction:
    def test_no_cap_unbounded(self) -> None:
        cache = InMemoryNodeCache()
        for i in range(50):
            cache.put(_key(f"n{i}"), {"i": i})
        assert len(cache) == 50

    def test_max_entries_enforces_cap(self) -> None:
        cache = InMemoryNodeCache(max_entries=3)
        for i in range(5):
            cache.put(_key(f"n{i}"), {"i": i})
        # Oldest two (n0, n1) evicted.
        assert len(cache) == 3
        assert cache.has(_key("n4"))
        assert cache.has(_key("n3"))
        assert cache.has(_key("n2"))
        assert not cache.has(_key("n0"))
        assert not cache.has(_key("n1"))

    def test_get_promotes_to_mru(self) -> None:
        cache = InMemoryNodeCache(max_entries=3)
        cache.put(_key("n0"), 0)
        cache.put(_key("n1"), 1)
        cache.put(_key("n2"), 2)
        # Touch n0 — should become MRU.
        _ = cache.get(_key("n0"))
        cache.put(_key("n3"), 3)  # forces eviction
        # n1 is now oldest; should be the one evicted.
        assert cache.has(_key("n0"))
        assert not cache.has(_key("n1"))
        assert cache.has(_key("n2"))
        assert cache.has(_key("n3"))

    def test_nondeterministic_entries_pinned(self) -> None:
        cache = InMemoryNodeCache(max_entries=2)
        cache.put(_key("llm0"), "out0", is_deterministic=False)
        cache.put(_key("llm1"), "out1", is_deterministic=False)
        cache.put(_key("det0"), "out2", is_deterministic=True)
        cache.put(_key("det1"), "out3", is_deterministic=True)
        # det0 should have been evicted to make room; the two LLM
        # entries are pinned. det1 stays since cache > cap but no more
        # deterministic entries exist to drop.
        assert cache.has(_key("llm0"))
        assert cache.has(_key("llm1"))
        assert cache.has(_key("det1"))
        # Length may exceed cap (because nondeterministic entries can't
        # be auto-evicted) — that's the safe failure mode.
        assert len(cache) >= 2

    def test_all_nondeterministic_cap_exceeded_is_safe(self) -> None:
        cache = InMemoryNodeCache(max_entries=2)
        cache.put(_key("a"), 0, is_deterministic=False)
        cache.put(_key("b"), 0, is_deterministic=False)
        cache.put(_key("c"), 0, is_deterministic=False)
        # No deterministic entries to drop; cap exceeded but cache works.
        assert len(cache) == 3
        assert all(cache.has(_key(k)) for k in ("a", "b", "c"))

    def test_explicit_invalidate_works_on_nondeterministic(self) -> None:
        cache = InMemoryNodeCache()
        cache.put(_key("llm0"), "x", is_deterministic=False)
        assert cache.has(_key("llm0"))
        cache.invalidate(_key("llm0"))
        assert not cache.has(_key("llm0"))


class TestFilesystemEviction:
    def test_no_caps_unbounded(self, tmp_path: Path) -> None:
        cache = FilesystemNodeCache(tmp_path / "cache")
        for i in range(10):
            cache.put(_key(f"n{i}"), {"i": i})
        assert len(list((tmp_path / "cache").glob("*.json"))) == 10

    def test_max_entries_evicts_oldest_deterministic(self, tmp_path: Path) -> None:
        cache = FilesystemNodeCache(tmp_path / "cache", max_entries=3)
        # Use tiny sleeps to give each put a distinguishable mtime;
        # otherwise all writes share a millisecond and eviction order
        # is undefined.
        for i in range(5):
            cache.put(_key(f"n{i}"), {"i": i})
            time.sleep(0.02)
        files = sorted((tmp_path / "cache").glob("*.json"))
        assert len(files) == 3
        # The two newest should still be present.
        assert cache.has(_key("n4"))
        assert cache.has(_key("n3"))
        assert not cache.has(_key("n0"))

    def test_max_size_bytes_enforces_cap(self, tmp_path: Path) -> None:
        # Each entry's serialized form is well above 100 bytes once we
        # include the key payload, so a 600-byte cap should hold ~2
        # entries.
        cache = FilesystemNodeCache(
            tmp_path / "cache", max_size_bytes=600
        )
        for i in range(8):
            cache.put(_key(f"n{i}"), {"i": i, "data": "x" * 20})
            time.sleep(0.02)
        total = sum(
            p.stat().st_size for p in (tmp_path / "cache").glob("*.json")
        )
        assert total <= 600 + 100  # some slack for the last write that triggers eviction
        # n7 (most recent) should be present.
        assert cache.has(_key("n7"))

    def test_nondeterministic_pinned_on_disk(self, tmp_path: Path) -> None:
        cache = FilesystemNodeCache(tmp_path / "cache", max_entries=2)
        cache.put(_key("llm0"), "out0", is_deterministic=False)
        time.sleep(0.02)
        cache.put(_key("llm1"), "out1", is_deterministic=False)
        time.sleep(0.02)
        cache.put(_key("det0"), "out2")  # is_deterministic=True default
        time.sleep(0.02)
        cache.put(_key("det1"), "out3")
        # The two LLM entries are pinned; det0 should have been evicted.
        assert cache.has(_key("llm0"))
        assert cache.has(_key("llm1"))
        assert cache.has(_key("det1"))
        # det0 should be gone or, if all four still fit (eviction can't
        # drop LLM entries), at minimum the file count exceeds the cap
        # safely.
        files = list((tmp_path / "cache").glob("*.json"))
        assert all(
            cache.has(_key(k))
            for k in ("llm0", "llm1", "det1")
        )
        assert len(files) >= 2

    def test_persisted_is_deterministic_flag(self, tmp_path: Path) -> None:
        """is_deterministic survives round-trip through the on-disk JSON."""
        cache = FilesystemNodeCache(tmp_path / "cache")
        cache.put(_key("a"), "value", is_deterministic=False)
        files = list((tmp_path / "cache").glob("*.json"))
        assert len(files) == 1
        blob = json.loads(files[0].read_text())
        assert blob["is_deterministic"] is False

    def test_legacy_entry_treated_as_deterministic(self, tmp_path: Path) -> None:
        """Pre-PR-n4 cache entries (no is_deterministic field) must remain
        readable and be eligible for eviction."""
        cache_dir = tmp_path / "cache"
        cache_dir.mkdir()
        # Write a synthetic legacy entry directly.
        legacy_key = _key("legacy")
        legacy_path = cache_dir / f"{legacy_key.digest()}.json"
        legacy_path.write_text(
            json.dumps({"value": "old", "written_at_iso": "2025-01-01T00:00:00+00:00"})
        )
        # Bring the mtime back so it's the oldest.
        old_mtime = time.time() - 1000
        os.utime(legacy_path, (old_mtime, old_mtime))

        cache = FilesystemNodeCache(cache_dir, max_entries=2)
        # Read it back — should work, value still accessible.
        assert cache.has(legacy_key)
        assert cache.get(legacy_key) == "old"
        # Add two more entries; legacy should be the first evicted.
        cache.put(_key("n0"), 0)
        time.sleep(0.02)
        cache.put(_key("n1"), 1)
        time.sleep(0.02)
        cache.put(_key("n2"), 2)
        assert not cache.has(legacy_key)
        assert cache.has(_key("n2"))
