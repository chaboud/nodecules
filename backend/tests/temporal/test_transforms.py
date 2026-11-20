"""Every change of state is a named transform (vault ADR-0038). Primitive
writes run as plain calls and leave a compact receipt; the full production
graph can be rebuilt from any receipt (boxing), and boxing changes the cost,
never the identity. The generator remembers what it confirmed, so a chain of
versions is not revisited on every produce."""

from __future__ import annotations

import asyncio

import pytest

from nodecules.core.activation import mark, marker_id
from nodecules.core.generation import RECIPE_ROLE, RECIPE_TEMPLATE_KIND, Generator, Realization, read
from nodecules.core.store import Edge, Node, Store, envelope_id
from nodecules.core.strip_access import mine
from nodecules.core.strip_nodes import strip_append, strip_close, strip_closed, strip_read, strip_trim
from nodecules.core.table import Participant, join, participant_id
from nodecules.core.transforms import WriteRefused, args_of, box, receipt, transform, verify

S = "room"


def test_an_append_leaves_a_compact_receipt_and_its_arguments_are_recovered_not_stored():
    store = Store()
    first = strip_append(store, S, "strips/mic", {"buf": "a" * 64}, author="mic")
    second = strip_append(store, S, "strips/mic", {"buf": "b" * 64}, {"buf": "c" * 64}, author="mic")
    r = receipt(store, second, "strips/mic")
    assert r["transform"] == "strip.append@1" and r["prior"] == first.hash_of("strips/mic")
    assert "args_value" not in r  # the buffers live once, in the strip
    prior, args = args_of(store, second, "strips/mic")
    assert prior.content_hash() == first.hash_of("strips/mic") and args == {"elements": [{"buf": "b" * 64}, {"buf": "c" * 64}]}
    assert second.note == "append 2 to strips/mic" and second.author == "mic"


@pytest.mark.parametrize("step", ["append-new", "append", "release", "close"])
def test_boxing_changes_the_cost_never_the_identity(step):
    store = Store()
    m = strip_append(store, S, "strips/obs", *[{"i": i} for i in range(5)], author="cam")
    if step == "append":
        m = strip_append(store, S, "strips/obs", {"i": 5}, author="cam")
    elif step == "release":
        m = strip_trim(store, S, "strips/obs", keep_last=2, author="cam")
    elif step == "close":
        m = strip_close(store, S, "strips/obs", author="cam")
    boxed = box(store, m, "strips/obs")
    assert boxed.content_hash() == m.hash_of("strips/obs")
    assert verify(store, m, "strips/obs")


def test_whole_value_writes_carry_their_value_and_verify_too():
    store = Store()
    strip_append(store, S, "strips/obs", {"i": 0}, author="cam")
    m = mark(store, S, "v1", author="dev")
    assert receipt(store, m, marker_id("v1"))["transform"] == "node.replace@1" and verify(store, m, marker_id("v1"))
    t = join(store, "table", Participant(id="ana", kind="human"))
    assert verify(store, t, participant_id("ana"))


def test_a_write_outside_any_transform_has_no_receipt_and_cannot_be_boxed():
    store = Store()
    tx = store.transaction(S, author="hand")
    tx.put(Node(id="loose", kind="k", scope=S, data=1))
    m = tx.commit()
    assert receipt(store, m, "loose") is None
    with pytest.raises(LookupError):
        verify(store, m, "loose")
    # a receipt left over from an earlier version does not vouch for a hand-written later one
    strip_append(store, S, "strips/obs", {"i": 0}, author="cam")
    tx = store.transaction(S, author="hand")
    tx.put(Node(id="strips/obs", kind="strip", scope=S, data=[{"i": 99}]))
    m = tx.commit()
    assert receipt(store, m, "strips/obs") is None


def test_receipts_replay_from_an_old_version_to_the_new_one():
    store = Store()
    versions = [strip_append(store, S, "strips/obs", {"i": 0}, author="cam")]
    versions.append(strip_append(store, S, "strips/obs", {"i": 1}, {"i": 2}, author="cam"))
    versions.append(strip_trim(store, S, "strips/obs", keep_last=2, author="cam"))
    versions.append(strip_close(store, S, "strips/obs", author="cam"))
    data = None
    for m in versions:
        prior, args = args_of(store, m, "strips/obs")
        assert (prior.data if prior else None) == data
        data = transform(receipt(store, m, "strips/obs")["transform"]).apply(data, args)
    final = store.get(versions[-1], "strips/obs")
    assert Node(id="strips/obs", kind="strip", scope=S, data=data).content_hash() == final.content_hash()


def test_a_closed_strip_refuses_appends_and_reads_past_its_end_say_ended():
    store = Store()
    strip_append(store, S, "strips/seg/1", {"i": 0}, {"i": 1}, author="cam")
    m = strip_close(store, S, "strips/seg/1", author="dock")
    assert strip_closed(store, m, "strips/seg/1") and [e["i"] for e in strip_read(store, m, "strips/seg/1")] == [0, 1]
    with pytest.raises(WriteRefused):
        strip_append(store, S, "strips/seg/1", {"i": 2}, author="cam")
    node = store.get(m, "strips/seg/1")
    r = read(mine(), node.data, None, position=2, kind="strip")
    assert r.reason == "ended" and "ended at element 1" in r.lost
    with pytest.raises(WriteRefused):
        strip_close(store, S, "strips/seg/1", author="dock")


# --- the generator's memo ------------------------------------------------------------


async def _append_cook(inputs, params):
    prev = inputs.get("prev")
    return {"elements": [*(prev["elements"] if prev else []), inputs["el"]]}


def _chain(store: Store, n: int, g: Generator) -> int:
    """Each version a production reading the previous one; returns how many
    single-node productions ran in total."""
    calls = 0
    original = g._produce_one

    async def counted(*a, **k):
        nonlocal calls
        calls += 1
        return await original(*a, **k)

    g._produce_one = counted  # type: ignore[method-assign]
    for k in range(n):
        tx = store.transaction("w", author="dev")
        tx.put(Node(id=f"in/{k}", kind="element", scope="w", data={"k": k}))
        edges = [Edge(target=f"in/{k}", role="el"), Edge(target="r/append", scope="lib", role=RECIPE_ROLE)]
        if k:
            edges.append(Edge(target=f"v{k - 1}", role="prev"))
        tx.put(Node(id=f"v{k}", kind="strip", scope="w", edges=tuple(edges)))
        tx.commit()
        out = asyncio.run(g.produce("w", f"v{k}"))
        assert len(out.node.data["elements"]) == k + 1
    return calls


def _lib(store: Store) -> Generator:
    tx = store.transaction("lib", author="dev")
    tx.put(Node(id="r/append", kind=RECIPE_TEMPLATE_KIND, scope="lib", data={"realization": "append@1", "params": {}}))
    tx.commit()
    return Generator(store, [Realization("append@1", _append_cook, deterministic=True)], author="gen")


def test_a_chain_of_versions_is_not_revisited_on_every_produce():
    store = Store()
    n = 60
    calls = _chain(store, n, _lib(store))
    assert calls <= 2 * n + 1  # each version and its element once, plus the recipe; was n(n+1)+n (spikes/boxing-bench B2)


def test_the_memo_never_serves_a_result_whose_inputs_changed():
    store = Store()
    g = _lib(store)
    _chain(store, 5, g)
    tx = store.transaction("w", author="dev")
    tx.put(Node(id="in/2", kind="element", scope="w", data={"k": "changed"}))
    tx.commit()
    out = asyncio.run(g.produce("w", "v4"))
    assert [e["k"] for e in out.node.data["elements"]] == [0, 1, "changed", 3, 4] and out.cooked
    again = asyncio.run(g.produce("w", "v4"))
    assert again.cache_hit and not again.cooked  # unchanged since: served as the cache hit it is
