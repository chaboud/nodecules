"""Activation: markers name versions; activating one is a forward commit."""

from __future__ import annotations

from pathlib import Path

import pytest

from nodecules.core.activation import activate, mark, marks
from nodecules.core.disk import DiskBacking, load
from nodecules.core.replica import sync, transfer
from nodecules.core.store import Node, Store

S = "keyhole/templates"


def _put(store: Store, name: str, data, author: str = "dev") -> None:
    tx = store.transaction(S, author=author)
    tx.put(Node(id=name, kind="template", scope=S, data=data))
    tx.commit(f"{name} = {data}")


def test_mark_and_activate_restore_content_by_a_forward_commit():
    s = Store()
    _put(s, "prompt", {"v": 1})
    _put(s, "vocab", ["cat", "dog"])
    factory = s.current(S)
    mark(s, S, "factory", note="shipped defaults")
    _put(s, "prompt", {"v": 2})
    _put(s, "prompt", {"v": 3}, author="inner-brain")
    _put(s, "extra", "added later")
    before = s.current(S)
    assert marks(s, before) == {"factory": factory.content_hash()}
    m = activate(s, S, "factory", author="alice")
    assert m.seq == before.seq + 1 and m.parent == before.content_hash()  # forward, not backward
    assert m.activated == factory.content_hash()
    assert s.get(m, "prompt").data == {"v": 1} and s.get(m, "vocab").data == ["cat", "dog"]
    assert s.get(m, "extra") is None  # not part of the factory version
    assert marks(s, m) == {"factory": factory.content_hash()}  # markers survive the rollback
    assert [x.seq for x in s.history(S)][:3] == [m.seq, before.seq, before.seq - 1]  # nothing rewritten
    # idempotent
    assert activate(s, S, "factory") is m
    # undo the rollback by activating the pre-rollback manifest by hash
    back = activate(s, S, before.content_hash(), author="alice")
    assert s.get(back, "prompt").data == {"v": 3} and s.get(back, "extra").data == "added later"
    assert back.activated == before.content_hash()
    with pytest.raises(ValueError):
        activate(s, S, "no-such-label")


def test_activation_binds_by_hash_without_loading_bodies_and_syncs(tmp_path: Path):
    a = Store()
    a.attach(DiskBacking(tmp_path / "a"))
    _put(a, "prompt", {"v": 1})
    mark(a, S, "factory")
    _put(a, "prompt", {"v": 2})
    fresh = load(tmp_path / "a")  # bodies on disk, nothing resident
    m = activate(fresh, S, "factory", author="ops")
    h = m.hash_of("prompt")
    assert fresh.residency(h) == "disk"  # restored without reading the body
    assert fresh.get(m, "prompt").data == {"v": 1}
    b = Store()
    transfer(fresh, b, S)
    b.set_head(S, fresh.current(S))
    sync(fresh, b, S)
    assert b.current(S).activated == m.activated
