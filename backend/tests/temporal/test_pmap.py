"""Tests for PR-r3a — the persistent map the node store stands on."""

from __future__ import annotations

import random

import pytest

from nodecules.core.pmap import PMap, diff, key_hash


def _keys(n: int) -> list:
    return [f"strips/turns/diarized#{i}" for i in range(n)]


def test_empty_map_has_no_keys_and_a_stable_root():
    pm = PMap()
    assert len(pm) == 0
    assert "x" not in pm
    assert pm.get("x") is None
    assert pm.root_hash == PMap().root_hash


def test_set_get_and_persistence_of_the_old_version():
    a = PMap().set("k", 1)
    b = a.set("k", 2).set("j", 3)
    assert a["k"] == 1 and "j" not in a and len(a) == 1
    assert b["k"] == 2 and b["j"] == 3 and len(b) == 2
    with pytest.raises(KeyError):
        a["j"]


def test_setting_the_same_value_returns_the_same_map():
    a = PMap().set("k", 1)
    assert a.set("k", 1) is a
    assert a.delete("nope") is a


def test_shape_is_independent_of_insertion_order():
    keys = _keys(3000)
    a = PMap.of({k: i for i, k in enumerate(keys)})
    shuffled = keys[:]
    random.Random(7).shuffle(shuffled)
    b = PMap()
    for k in shuffled:
        b = b.set(k, int(k.rsplit("#", 1)[1]))
    assert a.root_hash == b.root_hash
    assert a == b
    assert len(a) == len(b) == 3000


def test_shape_is_independent_of_history():
    """Insert-then-delete leaves the trie exactly as fresh insertion makes it;
    this is the canonical-form rule that makes replicas agree (P-26)."""
    base = PMap.of({k: 0 for k in _keys(500)})
    churned = base
    for k in _keys(2000)[500:]:
        churned = churned.set(k, 1)
    for k in _keys(2000)[500:]:
        churned = churned.delete(k)
    assert churned.root_hash == base.root_hash
    assert len(churned) == 500


def test_delete_collapses_to_the_fresh_shape_at_small_sizes():
    one = PMap().set("a", 1)
    two = one.set("b", 2)
    assert two.delete("b").root_hash == one.root_hash
    assert two.delete("a").delete("b").root_hash == PMap().root_hash


def test_root_hash_changes_iff_the_mapping_changes():
    a = PMap.of({"a": 1, "b": 2})
    assert a.set("a", 1).root_hash == a.root_hash
    assert a.set("a", 9).root_hash != a.root_hash
    assert a.set("c", 3).root_hash != a.root_hash
    assert a.delete("b").root_hash != a.root_hash


def test_structural_sharing_keeps_unchanged_subtrees_by_identity():
    a = PMap.of({k: 0 for k in _keys(2000)})
    b = a.set("only-this-one", 1)
    shared = sum(1 for x in a._root.children for y in b._root.children if x is y)
    # A single insert touches one root slot; every other slot is the same object.
    assert shared >= len(a._root.children) - 1


def test_iteration_order_is_deterministic_and_complete():
    keys = _keys(200)
    a = PMap.of({k: 1 for k in keys})
    b = PMap.of({k: 1 for k in reversed(keys)})
    assert list(a) == list(b)
    assert sorted(a.keys()) == sorted(keys)
    assert dict(a.items()) == {k: 1 for k in keys}


def test_key_hash_is_sha256_not_pythons_salted_hash():
    """Placement is a pure function of the key, the same on every machine."""
    import hashlib

    assert key_hash("audio.wav") == int.from_bytes(
        hashlib.sha256(b"audio.wav").digest(), "big"
    )


def test_diff_reports_added_removed_and_changed():
    a = PMap.of({k: 0 for k in _keys(1000)})
    b = a.set("strips/turns/diarized#5", 1).delete("strips/turns/diarized#7").set("new", 2)
    d = diff(a, b)
    assert d.added == {"new": 2}
    assert d.removed == {"strips/turns/diarized#7": 0}
    assert d.changed == {"strips/turns/diarized#5": (0, 1)}
    assert d.keys == {"new", "strips/turns/diarized#7", "strips/turns/diarized#5"}
    assert bool(d)
    assert not diff(a, a)
    assert not diff(a, PMap.of({k: 0 for k in _keys(1000)}))


def test_diff_is_symmetric_in_content():
    a = PMap.of({"a": 1, "b": 2})
    b = PMap.of({"b": 3, "c": 4})
    d = diff(a, b)
    assert d.removed == {"a": 1} and d.added == {"c": 4} and d.changed == {"b": (2, 3)}
    r = diff(b, a)
    assert r.added == {"a": 1} and r.removed == {"c": 4} and r.changed == {"b": (3, 2)}


def test_large_round_trip():
    entries = {f"n{i}": {"v": i} for i in range(20000)}
    pm = PMap.of(entries)
    assert len(pm) == 20000
    assert all(pm[k] == v for k, v in entries.items())
    assert dict(pm.items()) == entries
