"""An LLM as a realization, on a chat graph whose recipe is live-editable
and whose every turn leaves a receipt — the shape of the chat demo."""

from __future__ import annotations

import pytest

from nodecules.core.generation import PARAMS_ROLE, RECIPE_ROLE, RECIPE_TEMPLATE_KIND, Generator
from nodecules.core.llm_providers import MockToolProvider
from nodecules.core.llm_realization import llm_realization
from nodecules.core.store import Edge, Node, Store, envelope_id
from nodecules.core.strip_access import AllPattern
from nodecules.core.strip_nodes import strip_append, strip_read

CHAT = "chat/session-1"
LIB = "chat/library"


@pytest.mark.asyncio
async def test_chat_graph_with_a_live_editable_recipe_and_receipts():
    provider = MockToolProvider(responses=["Hello! How can I help?", "Sure — here is a haiku.", "Aye, matey."])
    store = Store()
    tx = store.transaction(LIB, author="dev")
    tx.put(Node(id="recipes/assistant", kind=RECIPE_TEMPLATE_KIND, scope=LIB, data={"realization": "llm.mock@1", "params": {"model": "mock-1", "temperature": 0.0}}))
    tx.commit("recipes")
    tx = store.transaction(CHAT, author="dev")
    tx.put(Node(id="params/persona", kind="params", scope=CHAT, data={"system": "You are a helpful assistant."}))
    tx.put(Node(id="reply", kind="chat.reply", scope=CHAT, edges=(Edge(target="strips/messages", pattern=AllPattern(), role="messages"), Edge(target="recipes/assistant", scope=LIB, role=RECIPE_ROLE), Edge(target="params/persona", role=PARAMS_ROLE))))
    tx.commit("declare the chat graph")
    g = Generator(store, [llm_realization(provider, "llm.mock@1")], author="chat")

    # turn 1
    strip_append(store, CHAT, "strips/messages", {"role": "user", "content": "hi"}, author="alice")
    out = await g.produce(CHAT, "reply")
    assert out.cooked and out.node.data["content"] == "Hello! How can I help?"
    assert provider.call_log[-1]["messages"][0] == {"role": "system", "content": "You are a helpful assistant."}
    assert provider.call_log[-1]["model"] == "mock-1"
    env = store.get(store.current(CHAT), envelope_id("reply")).data
    assert env["reproducibility"] == "equivalent" and env["recipe"]["realization"] == "llm.mock@1"  # unseeded: perturbing
    # asking again without a new message is a cache hit: no second call
    again = await g.produce(CHAT, "reply")
    assert again.cache_hit and len(provider.call_log) == 1

    # turn 2: a new message recooks
    strip_append(store, CHAT, "strips/messages", {"role": "assistant", "content": out.node.data["content"]}, {"role": "user", "content": "write a haiku"}, author="alice")
    out2 = await g.produce(CHAT, "reply")
    assert out2.cooked and out2.node.data["content"].startswith("Sure")
    assert len(provider.call_log[-1]["messages"]) == 4  # system + 3 turns
    assert strip_read(store, store.current(CHAT), "strips/messages")[-1]["content"] == "write a haiku"

    # editing the persona mid-session is a commit; the next production uses it, and the receipt shows why
    tx = store.transaction(CHAT, author="alice")
    tx.put(Node(id="params/persona", kind="params", scope=CHAT, data={"system": "You are a pirate."}))
    tx.commit("change persona")
    out3 = await g.produce(CHAT, "reply")
    assert out3.cooked and out3.node.data["content"] == "Aye, matey."
    assert provider.call_log[-1]["messages"][0]["content"] == "You are a pirate."
    assert out3.cache_key != out2.cache_key
    # observability: who changed what, and every turn's receipt
    assert store.blame(CHAT, "params/persona").author == "alice"
    assert [m.note for m in store.history(CHAT)][:1] == ["produce reply"]


def test_strips_as_nodes_are_copy_on_write():
    store = Store()
    m1 = strip_append(store, CHAT, "strips/log", 1, 2)
    m2 = strip_append(store, CHAT, "strips/log", 3)
    assert strip_read(store, m1, "strips/log") == [1, 2]
    assert strip_read(store, m2, "strips/log") == [1, 2, 3]
    assert strip_read(store, store.current(LIB), "strips/log") == []
