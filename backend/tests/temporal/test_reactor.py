"""Change-driven production: a reactor keeps targets fresh when anything
they read changes, by commit, merge, or pull; it ignores its own commits,
coalesces changes that arrive mid-run, and stops a target that feeds its
own inputs instead of looping on a model."""

from __future__ import annotations

import asyncio

import pytest

from nodecules.core.deferral import fulfill, requests
from nodecules.core.generation import PARAMS_ROLE, RECIPE_ROLE, RECIPE_TEMPLATE_KIND, Generator, Realization
from nodecules.core.reactor import Reactor, Runaway, reads_of
from nodecules.core.replica import sync
from nodecules.core.store import Edge, Node, Store
from nodecules.core.strip_access import AllPattern
from nodecules.core.strip_nodes import strip_append, strip_read

CHAT, LIB = "chat", "lib"


def _chat(store: Store) -> None:
    tx = store.transaction(LIB, author="dev")
    tx.put(Node(id="recipes/reply", kind=RECIPE_TEMPLATE_KIND, scope=LIB, data={"realization": "echo@1", "params": {"prefix": "re:"}}))
    tx.commit()
    tx = store.transaction(CHAT, author="dev")
    tx.put(Node(id="params/style", kind="params", scope=CHAT, data={"suffix": "."}))
    tx.put(Node(id="reply", kind="chat.reply", scope=CHAT, edges=(Edge(target="strips/messages", pattern=AllPattern(), role="messages"), Edge(target="recipes/reply", scope=LIB, role=RECIPE_ROLE), Edge(target="params/style", role=PARAMS_ROLE))))
    tx.commit()


def _echo(counter, gate=None, entered=None):
    async def cook(inputs, params):
        if entered is not None:
            entered.set()
        if gate is not None:
            await gate.wait()
        counter.append(1)
        msgs = inputs.get("messages") or []
        last = msgs[-1]["content"] if msgs else ""
        return {"content": f"{params['prefix']}{last}{params.get('suffix', '')}"}

    return Realization("echo@1", cook)


@pytest.mark.asyncio
async def test_a_new_message_recooks_the_reply_without_anyone_asking():
    store = Store()
    _chat(store)
    strip_append(store, CHAT, "strips/messages", {"role": "user", "content": "hi"}, author="alice")
    cooks = []
    r = Reactor(Generator(store, [_echo(cooks)], author="reactor"), [(CHAT, "reply")]).start()
    await r.settle()
    assert r.runs == 1 and len(cooks) == 1 and r.last[(CHAT, "reply")].node.data["content"] == "re:hi."
    strip_append(store, CHAT, "strips/messages", {"role": "user", "content": "how are you"}, author="alice")
    await r.settle()
    assert r.runs == 2 and len(cooks) == 2
    assert store.get(store.current(CHAT), "reply").data["content"] == "re:how are you."
    await r.settle()
    assert r.runs == 2  # its own commit did not trigger it again


@pytest.mark.asyncio
async def test_a_recipe_or_params_change_recooks_and_an_unrelated_change_does_not():
    store = Store()
    _chat(store)
    strip_append(store, CHAT, "strips/messages", {"role": "user", "content": "hi"}, author="alice")
    cooks = []
    r = Reactor(Generator(store, [_echo(cooks)], author="reactor"), [(CHAT, "reply")]).start()
    await r.settle()
    tx = store.transaction(CHAT, author="bob")
    tx.put(Node(id="notes/unrelated", kind="note", scope=CHAT, data="nothing to do with the reply"))
    tx.commit()
    tx = store.transaction("elsewhere", author="bob")
    tx.put(Node(id="x", kind="k", scope="elsewhere", data=1))
    tx.commit()
    await r.settle()
    assert r.runs == 1 and len(cooks) == 1
    tx = store.transaction(LIB, author="dev")  # the recipe is in another scope; it is read, so it is watched
    tx.put(Node(id="recipes/reply", kind=RECIPE_TEMPLATE_KIND, scope=LIB, data={"realization": "echo@1", "params": {"prefix": "RE:"}}))
    tx.commit()
    await r.settle()
    assert r.runs == 2 and store.get(store.current(CHAT), "reply").data["content"] == "RE:hi."
    tx = store.transaction(CHAT, author="dev")
    tx.put(Node(id="params/style", kind="params", scope=CHAT, data={"suffix": "!"}))
    tx.commit()
    await r.settle()
    assert r.runs == 3 and store.get(store.current(CHAT), "reply").data["content"] == "RE:hi!"


@pytest.mark.asyncio
async def test_changes_that_arrive_mid_run_coalesce_into_one_more_run():
    store = Store()
    _chat(store)
    strip_append(store, CHAT, "strips/messages", {"role": "user", "content": "one"}, author="alice")
    cooks, gate, entered = [], asyncio.Event(), asyncio.Event()
    gate.set()
    r = Reactor(Generator(store, [_echo(cooks, gate, entered)], author="reactor"), [(CHAT, "reply")]).start()
    await r.settle()
    gate.clear()  # the next cook blocks
    entered.clear()
    strip_append(store, CHAT, "strips/messages", {"role": "user", "content": "two"}, author="alice")
    await asyncio.wait_for(entered.wait(), 2)  # the run has read "two" and is cooking
    for word in ("three", "four", "five"):
        strip_append(store, CHAT, "strips/messages", {"role": "user", "content": word}, author="alice")
    gate.set()
    await r.settle()
    assert r.runs == 3 and len(cooks) == 3  # the first, the blocked one, and one more covering three, four, five
    assert store.get(store.current(CHAT), "reply").data["content"] == "re:five."


@pytest.mark.asyncio
async def test_an_input_that_does_not_exist_yet_is_watched_and_its_arrival_triggers():
    store = Store()
    tx = store.transaction(LIB, author="dev")
    tx.put(Node(id="recipes/count", kind=RECIPE_TEMPLATE_KIND, scope=LIB, data={"realization": "count@1", "params": {}}))
    tx.commit()
    tx = store.transaction(CHAT, author="dev")
    tx.put(Node(id="summary", kind="k", scope=CHAT, edges=(Edge(target="strips/notes", pattern=AllPattern(), role="notes", optional=True), Edge(target="recipes/count", scope=LIB, role=RECIPE_ROLE))))
    tx.commit()

    async def count(inputs, params):
        return {"notes": len(inputs.get("notes") or [])}

    r = Reactor(Generator(store, [Realization("count@1", count)], author="reactor"), [(CHAT, "summary")]).start()
    await r.settle()
    assert r.last[(CHAT, "summary")].node.data == {"notes": 0} and (CHAT, "strips/notes") in r.reads
    strip_append(store, CHAT, "strips/notes", "first note", author="carol")
    await r.settle()
    assert r.runs == 2 and store.get(store.current(CHAT), "summary").data == {"notes": 1}


@pytest.mark.asyncio
async def test_a_change_that_arrives_by_replica_sync_triggers_like_a_commit():
    here, there = Store(), Store()
    _chat(here)
    strip_append(here, CHAT, "strips/messages", {"role": "user", "content": "hi"}, author="alice")
    for scope in (LIB, CHAT):
        sync(there, here, scope)
    cooks = []
    r = Reactor(Generator(here, [_echo(cooks)], author="reactor"), [(CHAT, "reply")]).start()
    await r.settle()
    strip_append(there, CHAT, "strips/messages", {"role": "user", "content": "from the other machine"}, author="dana")
    sync(here, there, CHAT)  # a head move here, not a commit
    await r.settle()
    assert r.runs == 2 and here.get(here.current(CHAT), "reply").data["content"] == "re:from the other machine."


@pytest.mark.asyncio
async def test_a_target_that_feeds_its_own_inputs_is_stopped_and_named():
    store = Store()
    _chat(store)
    strip_append(store, CHAT, "strips/messages", {"role": "user", "content": "hi"}, author="alice")
    cooks = []

    def append_reply(result):
        strip_append(store, CHAT, "strips/messages", {"role": "assistant", "content": result[(CHAT, "reply")].node.data["content"]}, author="assistant")

    r = Reactor(Generator(store, [_echo(cooks)], author="reactor"), [(CHAT, "reply")], on_result=append_reply, max_runs=5).start()
    await r.settle()
    assert isinstance(r.error, Runaway) and "feeding its own inputs" in str(r.error) and r.runs == 5
    calm = Store()
    _chat(calm)
    strip_append(calm, CHAT, "strips/messages", {"role": "user", "content": "hi"}, author="alice")

    def append_calm(result):
        strip_append(calm, CHAT, "strips/messages", {"role": "assistant", "content": result[(CHAT, "reply")].node.data["content"]}, author="assistant")

    r2 = Reactor(Generator(calm, [_echo([])], author="reactor"), [(CHAT, "reply")], ignore_authors={"assistant"}, on_result=append_calm).start()
    await r2.settle()
    strip_append(calm, CHAT, "strips/messages", {"role": "user", "content": "again"}, author="alice")
    await r2.settle()
    assert r2.error is None and r2.runs == 2 and [m["role"] for m in strip_read(calm, calm.current(CHAT), "strips/messages")] == ["user", "assistant", "user", "assistant"]


@pytest.mark.asyncio
async def test_a_fulfilment_from_another_machine_moves_the_graph_on_by_itself():
    """A step deferred to a machine with a model; when its answer lands, the
    step downstream of it cooks here without anyone asking."""
    store = Store()
    tx = store.transaction(LIB, author="dev")
    tx.put(Node(id="recipes/think", kind=RECIPE_TEMPLATE_KIND, scope=LIB, data={"realization": "llm.spark@1", "params": {}}))
    tx.put(Node(id="recipes/shout", kind=RECIPE_TEMPLATE_KIND, scope=LIB, data={"realization": "shout@1", "params": {}}))
    tx.commit()
    tx = store.transaction(CHAT, author="dev")
    tx.put(Node(id="question", kind="k", scope=CHAT, data="what is two and two"))
    tx.put(Node(id="answer", kind="k", scope=CHAT, edges=(Edge(target="question", role="q"), Edge(target="recipes/think", scope=LIB, role=RECIPE_ROLE))))
    tx.put(Node(id="loud", kind="k", scope=CHAT, edges=(Edge(target="answer", role="a"), Edge(target="recipes/shout", scope=LIB, role=RECIPE_ROLE))))
    tx.commit()

    async def shout(inputs, params):
        return str(inputs["a"]).upper()

    r = Reactor(Generator(store, [Realization("shout@1", shout)], author="reactor", defer=True), [(CHAT, "loud")]).start()
    await r.settle()
    assert r.last[(CHAT, "loud")].outcome == "pending" and len(requests(store, CHAT)) == 1
    fulfill(store, CHAT, "answer", "four", by="spark", realization="llm.spark@1")
    await r.settle()
    assert r.runs == 2 and r.last[(CHAT, "loud")].cooked and store.get(store.current(CHAT), "loud").data == "FOUR"


@pytest.mark.asyncio
async def test_stop_detaches_and_reads_cover_the_whole_closure():
    store = Store()
    _chat(store)
    strip_append(store, CHAT, "strips/messages", {"role": "user", "content": "hi"}, author="alice")
    r = Reactor(Generator(store, [_echo([])], author="reactor"), [(CHAT, "reply")]).start()
    await r.settle()
    assert reads_of(store, [(CHAT, "reply")]) == {(CHAT, "reply"), (CHAT, "strips/messages"), (CHAT, "params/style"), (LIB, "recipes/reply")}
    r.stop()
    strip_append(store, CHAT, "strips/messages", {"role": "user", "content": "unheard"}, author="alice")
    await r.settle()
    assert r.runs == 1
