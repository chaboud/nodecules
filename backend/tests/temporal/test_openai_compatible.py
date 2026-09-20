"""The OpenAI-compatible provider, against a fake server: request shape and
response parsing. The first real model behind it is the Spark's job."""

from __future__ import annotations

import json
import threading
from http.server import BaseHTTPRequestHandler, HTTPServer

import pytest

from nodecules.core.llm_providers import ToolSchema
from nodecules.core.openai_compatible import OpenAICompatibleProvider

SEEN = []


class Fake(BaseHTTPRequestHandler):
    def do_POST(self):  # noqa: N802
        n = int(self.headers.get("Content-Length") or 0)
        body = json.loads(self.rfile.read(n))
        SEEN.append((self.path, dict(self.headers), body))
        if body["messages"][-1]["content"] == "think":
            # a thinking model that spent the budget before answering (the Spark, 2026-09-19)
            reply = {"choices": [{"message": {"role": "assistant", "content": "", "reasoning_content": "hmm " * 10}, "finish_reason": "length"}]}
        elif body.get("tools"):
            reply = {"choices": [{"message": {"role": "assistant", "content": "", "tool_calls": [{"id": "c1", "type": "function", "function": {"name": "lookup", "arguments": json.dumps({"q": "kyoto"})}}]}, "finish_reason": "tool_calls"}]}
        else:
            reply = {"choices": [{"message": {"role": "assistant", "content": f"echo:{body['messages'][-1]['content']}"}, "finish_reason": "stop"}], "usage": {"total_tokens": 7}}
        data = json.dumps(reply).encode()
        self.send_response(200)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(data)))
        self.end_headers()
        self.wfile.write(data)

    def log_message(self, *a):  # quiet
        return


@pytest.fixture
def server():
    srv = HTTPServer(("127.0.0.1", 0), Fake)
    t = threading.Thread(target=srv.serve_forever, daemon=True)
    t.start()
    yield f"http://127.0.0.1:{srv.server_port}"
    srv.shutdown()


@pytest.mark.asyncio
async def test_plain_chat_and_tool_calls_round_trip(server):
    SEEN.clear()
    p = OpenAICompatibleProvider(server, api_key="secret", extra_headers={"X-Trace": "1"})
    r = await p.generate_with_tools([{"role": "user", "content": "hi"}], model="local-7b", temperature=0.0, max_tokens=64)
    assert r.content == "echo:hi" and r.stop_reason == "end_turn" and r.tool_calls == []
    path, headers, body = SEEN[-1]
    assert path == "/v1/chat/completions" and headers["Authorization"] == "Bearer secret" and headers["X-Trace"] == "1"
    assert body["model"] == "local-7b" and body["temperature"] == 0.0 and body["max_tokens"] == 64 and "tools" not in body
    r = await p.generate_with_tools([{"role": "user", "content": "find"}], tools=[ToolSchema(name="lookup", description="look", parameters={"type": "object"})], model="local-7b")
    assert r.stop_reason == "tool_use" and r.tool_calls[0].name == "lookup" and r.tool_calls[0].arguments == {"q": "kyoto"}
    assert SEEN[-1][2]["tools"][0]["function"]["name"] == "lookup"
    assert p.last_raw["choices"][0]["finish_reason"] == "tool_calls"


@pytest.mark.asyncio
async def test_extra_body_rides_every_request(server):
    """Engine-specific fields (llama.cpp's chat_template_kwargs to switch a
    thinking model's reasoning off) are operator configuration merged into
    every payload; the graph never sees them. Found on the Spark: a thinking
    model returned content "" with finish "length" without it."""
    SEEN.clear()
    p = OpenAICompatibleProvider(server, extra_body={"chat_template_kwargs": {"enable_thinking": False}})
    r = await p.generate_with_tools([{"role": "user", "content": "hi"}], model="m")
    assert r.content == "echo:hi"
    body = SEEN[-1][2]
    assert body["chat_template_kwargs"] == {"enable_thinking": False}
    assert body["model"] == "m" and "messages" in body
    plain = OpenAICompatibleProvider(server)
    await plain.generate_with_tools([{"role": "user", "content": "hi"}], model="m")
    assert "chat_template_kwargs" not in SEEN[-1][2]


@pytest.mark.asyncio
async def test_a_thinking_models_reasoning_is_kept_apart_so_an_empty_answer_can_say_where_the_budget_went(server):
    p = OpenAICompatibleProvider(server)
    r = await p.generate_with_tools([{"role": "user", "content": "think"}], model="m", max_tokens=40)
    assert r.content == "" and r.tool_calls == [] and r.stop_reason == "max_tokens"
    assert r.reasoning == "hmm " * 10
