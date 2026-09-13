"""A chat with a live configurable graph and observational abilities.

The processing graph is nodes in the store: the messages are a strip, the
reply is produced through a recipe template with a persona in a params
node. Editing the persona is a commit; the next turn uses it and the
receipt says so. Nothing is hidden in the engine: every production leaves
an envelope, and history says who changed what.

Does not show: a real model (an echo provider stands in — pass any
ToolAwareProvider to `build()`), tool use, a table, or several people.

    cd backend && PYTHONPATH=. python3 ../demos/chat_live_graph.py
"""

from __future__ import annotations

import asyncio
import os
import sys

sys.path.insert(0, os.path.join(os.path.dirname(__file__)))

from common import EchoProvider, receipt_line  # noqa: E402

from nodecules.core.generation import PARAMS_ROLE, RECIPE_ROLE, RECIPE_TEMPLATE_KIND, Generator  # noqa: E402
from nodecules.core.llm_providers import ToolAwareProvider  # noqa: E402
from nodecules.core.llm_realization import llm_realization  # noqa: E402
from nodecules.core.store import Edge, Node, Store  # noqa: E402
from nodecules.core.strip_access import AllPattern  # noqa: E402
from nodecules.core.strip_nodes import strip_append, strip_read  # noqa: E402

CHAT = "chat/session-1"
LIB = "chat/library"
HANDLE = "llm.echo@1"


def build(provider: ToolAwareProvider) -> tuple[Store, Generator]:
    store = Store()
    tx = store.transaction(LIB, author="dev")
    tx.put(Node(id="recipes/assistant", kind=RECIPE_TEMPLATE_KIND, scope=LIB, data={"realization": HANDLE, "params": {"model": "echo", "temperature": 0.0}}))
    tx.commit("recipes")
    tx = store.transaction(CHAT, author="dev")
    tx.put(Node(id="params/persona", kind="params", scope=CHAT, data={"system": "You are a helpful assistant."}))
    tx.put(
        Node(
            id="reply",
            kind="chat.reply",
            scope=CHAT,
            edges=(
                Edge(target="strips/messages", pattern=AllPattern(), role="messages"),
                Edge(target="recipes/assistant", scope=LIB, role=RECIPE_ROLE),
                Edge(target="params/persona", role=PARAMS_ROLE),
            ),
        )
    )
    tx.commit("declare the chat graph")
    return store, Generator(store, [llm_realization(provider, HANDLE)], author="chat")


async def turn(store: Store, g: Generator, who: str, text: str, *, ask_twice: bool = False) -> None:
    strip_append(store, CHAT, "strips/messages", {"role": "user", "content": text}, author=who)
    out = await g.produce(CHAT, "reply")
    print(f"{who}: {text}")
    print(f"assistant: {out.node.data['content']}")
    print(receipt_line(out))
    if ask_twice:
        print("  (asking for the same reply again, nothing changed)")
        print(receipt_line(await g.produce(CHAT, "reply")))
    # the reply joins the conversation; from here the strip has changed and the next production must recook
    strip_append(store, CHAT, "strips/messages", {"role": "assistant", "content": out.node.data["content"]}, author="assistant")


async def main() -> None:
    store, g = build(EchoProvider())
    await turn(store, g, "alice", "hi", ask_twice=True)

    await turn(store, g, "alice", "what can you do?")

    print("\n(alice edits the persona mid-session — a commit, not a restart)")
    tx = store.transaction(CHAT, author="alice")
    tx.put(Node(id="params/persona", kind="params", scope=CHAT, data={"system": "You are a pirate."}))
    m = tx.commit("change persona")
    print(f"  committed manifest seq {m.seq} by {m.author}: {m.note}")
    await turn(store, g, "alice", "and now?")

    print("\nobservability")
    print(f"  who last changed the persona: {store.blame(CHAT, 'params/persona').author}")
    print(f"  messages in the strip: {len(strip_read(store, store.current(CHAT), 'strips/messages'))}")
    print("  history (newest first):")
    for man in list(store.history(CHAT))[:6]:
        print(f"    seq {man.seq:>2} · {man.author or '-':<9} · {man.note}")
    print(f"  productions that actually ran a model: {g.cooks}")


if __name__ == "__main__":
    asyncio.run(main())
