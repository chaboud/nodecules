"""Tests for PR-n9 tool-aware provider abstraction.

Validates the protocol shape and the Mock implementation. Concrete
provider tests (Ollama, Anthropic, Bedrock) land in PR-n9b with a
real test rig that can hit each API against canned fixtures.
"""

from __future__ import annotations

import pytest

from nodecules.core.llm_providers import (
    MockToolProvider,
    ToolAwareProvider,
    ToolCall,
    ToolCallResponse,
    ToolSchema,
)


class TestMockReturnsScriptedResponses:
    async def test_returns_text_per_call_index(self) -> None:
        provider = MockToolProvider(responses=["first", "second", "third"])
        r1 = await provider.generate_with_tools(
            messages=[{"role": "user", "content": "hi"}],
            model="mock",
        )
        r2 = await provider.generate_with_tools(
            messages=[{"role": "user", "content": "hi"}],
            model="mock",
        )
        r3 = await provider.generate_with_tools(
            messages=[{"role": "user", "content": "hi"}],
            model="mock",
        )
        assert r1.content == "first"
        assert r2.content == "second"
        assert r3.content == "third"

    async def test_default_content_when_exhausted(self) -> None:
        provider = MockToolProvider(
            responses=["one"], default_content="fallback"
        )
        r1 = await provider.generate_with_tools(messages=[], model="mock")
        r2 = await provider.generate_with_tools(messages=[], model="mock")
        assert r1.content == "one"
        assert r2.content == "fallback"

    async def test_stop_reason_default_end_turn(self) -> None:
        provider = MockToolProvider(responses=["x"])
        r = await provider.generate_with_tools(messages=[], model="mock")
        assert r.stop_reason == "end_turn"
        assert r.tool_calls == []


class TestMockScriptsToolCalls:
    async def test_scripted_tool_call_sets_stop_reason(self) -> None:
        tc = ToolCall(name="list_speakers", arguments={"order": "speaking_time_desc"})
        provider = MockToolProvider(tool_call_scripts=[[tc]])
        r = await provider.generate_with_tools(
            messages=[],
            tools=[
                ToolSchema(
                    name="list_speakers",
                    description="list",
                    parameters={"type": "object", "properties": {}},
                )
            ],
            model="mock",
        )
        assert r.stop_reason == "tool_use"
        assert len(r.tool_calls) == 1
        assert r.tool_calls[0].name == "list_speakers"
        assert r.tool_calls[0].arguments == {"order": "speaking_time_desc"}

    async def test_alternating_tool_then_text(self) -> None:
        """Simulate stenota's agent loop: first call requests a tool,
        second call (after dispatch + tool result) emits final text."""
        tc = ToolCall(name="meeting_overview", arguments={})
        provider = MockToolProvider(
            responses=["", "Final answer based on tool result"],
            tool_call_scripts=[[tc], []],
        )
        r1 = await provider.generate_with_tools(messages=[], model="mock")
        assert r1.stop_reason == "tool_use"
        assert r1.tool_calls[0].name == "meeting_overview"
        # Caller would dispatch the tool, append a {"role": "tool"} message,
        # then call again.
        r2 = await provider.generate_with_tools(messages=[], model="mock")
        assert r2.stop_reason == "end_turn"
        assert r2.content == "Final answer based on tool result"
        assert r2.tool_calls == []


class TestCallLog:
    async def test_logs_record_messages_tools_model(self) -> None:
        provider = MockToolProvider(responses=["x"])
        tools = [
            ToolSchema(
                name="t",
                description="",
                parameters={"type": "object", "properties": {}},
            )
        ]
        msgs = [{"role": "user", "content": "hi"}]
        await provider.generate_with_tools(
            messages=msgs,
            tools=tools,
            model="foo",
            temperature=0.5,
            max_tokens=512,
        )
        entry = provider.call_log[0]
        assert entry["messages"] == msgs
        assert entry["tools"] == tools
        assert entry["model"] == "foo"
        assert entry["temperature"] == 0.5
        assert entry["max_tokens"] == 512

    async def test_response_schema_logged(self) -> None:
        provider = MockToolProvider(responses=["{}"])
        schema = {"type": "object", "properties": {"x": {"type": "string"}}}
        await provider.generate_with_tools(
            messages=[],
            response_schema=schema,
            model="mock",
        )
        assert provider.call_log[0]["response_schema"] == schema


class TestCapabilityFlags:
    async def test_default_supports_both(self) -> None:
        provider = MockToolProvider()
        assert provider.supports_tool_use is True
        assert provider.supports_response_schema is True

    async def test_disable_tool_use(self) -> None:
        provider = MockToolProvider(supports_tool_use=False)
        assert provider.supports_tool_use is False

    async def test_disable_response_schema(self) -> None:
        provider = MockToolProvider(supports_response_schema=False)
        assert provider.supports_response_schema is False


class TestProtocolShape:
    def test_mock_is_a_provider(self) -> None:
        assert isinstance(MockToolProvider(), ToolAwareProvider)

    def test_tool_schema_is_hashable(self) -> None:
        """Frozen dataclasses are hashable; this lets tools be set members
        for de-dup at the agent layer."""
        ts = ToolSchema(
            name="t",
            description="",
            parameters={"type": "object", "properties": {}},
        )
        # Dict params are mutable so equality is by name/desc only at
        # the dataclass level. We at least verify identity-based hashing
        # works for set membership.
        s = set()
        s.add(ts)
        assert ts in s

    def test_tool_call_frozen(self) -> None:
        tc = ToolCall(name="x", arguments={})
        with pytest.raises(Exception):
            tc.name = "y"  # type: ignore[misc]
