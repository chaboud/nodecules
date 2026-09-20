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


@pytest.mark.asyncio
async def test_a_node_without_messages_is_put_to_the_model_as_its_inputs_rendered_by_role():
    """A deferred `consider/...` step has a decision, not a chat, in front of
    the model. The realization renders every input by role as one user
    message, so the same wrapper serves both shapes."""
    from nodecules.core.llm_realization import render_inputs

    provider = MockToolProvider(responses=["Take the cheap one."])
    store = Store()
    tx = store.transaction(LIB, author="dev")
    tx.put(Node(id="recipes/consider", kind=RECIPE_TEMPLATE_KIND, scope=LIB, data={"realization": "llm.mock@1", "params": {"system": "Advise.", "model": "mock-1"}}))
    tx.commit()
    tx = store.transaction(CHAT, author="dev")
    tx.put(Node(id="facts", kind="facts", scope=CHAT, data={"b": 2, "a": [1, 2]}))
    tx.put(Node(id="consider", kind="llm.consideration", scope=CHAT, edges=(Edge(target="facts", role="facts"), Edge(target="recipes/consider", scope=LIB, role=RECIPE_ROLE))))
    tx.commit()
    out = await Generator(store, [llm_realization(provider, "llm.mock@1")]).produce(CHAT, "consider")
    assert out.cooked and out.node.data["content"] == "Take the cheap one."
    sent = provider.call_log[-1]["messages"]
    assert sent[0] == {"role": "system", "content": "Advise."}
    assert sent[1]["role"] == "user" and sent[1]["content"] == render_inputs({"facts": {"b": 2, "a": [1, 2]}})
    assert sent[1]["content"].startswith("## facts\n") and '"a": [' in sent[1]["content"]
    assert render_inputs({"messages": []}) == "(no inputs)"


@pytest.mark.asyncio
async def test_an_empty_answer_is_a_failed_production_that_says_where_the_tokens_went():
    """Found on the Spark, 2026-09-19: a thinking model spent the recipe's
    max_tokens on reasoning and returned content "" with finish length,
    and the worker filed that as fulfilled. Now the realization refuses:
    the production fails with an error naming the reasoning length, and
    nothing is stored to become a cache hit."""
    from typing import Any, Dict, List, Optional

    from nodecules.core.llm_providers import ToolAwareProvider, ToolCallResponse, ToolSchema

    class Thinker(ToolAwareProvider):
        async def generate_with_tools(self, messages: List[Dict[str, Any]], *, tools: Optional[List[ToolSchema]] = None, response_schema: Optional[Dict[str, Any]] = None, model: str, temperature: float = 0.2, max_tokens: int = 4_096) -> ToolCallResponse:
            return ToolCallResponse(content="", tool_calls=[], stop_reason="max_tokens", raw={}, reasoning="let me think " * 40)

        @property
        def supports_tool_use(self) -> bool:
            return False

        @property
        def supports_response_schema(self) -> bool:
            return False

    store = Store()
    tx = store.transaction(LIB, author="dev")
    tx.put(Node(id="recipes/assistant", kind=RECIPE_TEMPLATE_KIND, scope=LIB, data={"realization": "llm.think@1", "params": {"model": "t", "max_tokens": 600}}))
    tx.commit()
    tx = store.transaction(CHAT, author="dev")
    tx.put(Node(id="reply", kind="chat.reply", scope=CHAT, edges=(Edge(target="strips/messages", pattern=AllPattern(), role="messages"), Edge(target="recipes/assistant", scope=LIB, role=RECIPE_ROLE))))
    tx.commit()
    strip_append(store, CHAT, "strips/messages", {"role": "user", "content": "hi"}, author="alice")
    out = await Generator(store, [llm_realization(Thinker(), "llm.think@1")]).produce(CHAT, "reply")
    assert out.outcome == "failed" and not out.cooked
    assert out.error.startswith("EmptyAnswer:") and "reasoning=520 chars" in out.error and "max_tokens=600" in out.error and "switch thinking off" in out.error
    assert store.get(store.current(CHAT), envelope_id("reply")) is None  # nothing stored, nothing to hit
