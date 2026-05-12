"""Tool-aware LLM provider adapters (PR-n9).

Lives alongside `core/smart_context.py`'s chat-shaped adapters; doesn't
replace them. The chat adapters are tuned for the `(context_data,
new_message) -> (response, updated_context)` shape that powers nodecules'
`SmartChatNode` / `ImmutableChatNode`. The tool-aware adapters here are
tuned for stenota's `LensAgent` shape: one LLM call with a list of
tool schemas + optional response schema, returning a structured
`ToolCallResponse` that the caller's agent loop dispatches.

**Scope of this PR (PR-n9).** Abstraction + Mock implementation only.
Concrete provider implementations (Ollama, Anthropic, Bedrock) land in
PR-n9b with a real test rig that can hit each provider against
canned test fixtures. The abstraction's shape is locked here.

Why not extend the existing `BaseProviderAdapter`? Two reasons:

1. The chat shape conflates message history (provider input) with
   updated context (provider output). The tool shape needs `tool_calls`
   as a first-class output channel; bolting it onto the tuple-return
   muddles both APIs.
2. The existing adapters live in a module (`smart_context.py`) tightly
   coupled to the FastAPI `SmartContext` table. The tool adapters need
   to work from a CLI / MCP server / library import without any DB
   dependency. Keeping them in a separate module preserves the
   nodecules-core-runs-without-Postgres invariant.

Once both adapter families exist, a single `ProviderAdapter` protocol
satisfying both contracts becomes practical. Premature today.
"""

from __future__ import annotations

from abc import ABC, abstractmethod
from dataclasses import dataclass, field
from typing import Any, Dict, List, Literal, Optional


StopReason = Literal["end_turn", "tool_use", "max_tokens", "stop_sequence", "refusal"]


@dataclass(frozen=True)
class ToolSchema:
    """JSON-schema description of a tool the LLM can invoke.

    The shape matches OpenAI's function-call schema (also used by Ollama
    and Anthropic's tool-use after a thin translation). Bedrock's converse
    API uses a structurally identical shape too. Adapter translation
    happens inside each provider implementation; callers stay
    provider-agnostic.
    """

    name: str
    description: str
    parameters: Dict[str, Any]  # JSON Schema for the args object


@dataclass(frozen=True)
class ToolCall:
    """One requested tool invocation parsed out of a provider response.

    `call_id` lets the caller correlate the response with the original
    request when posting results back (Anthropic's tool_use_id flow,
    OpenAI's tool_call_id). `name` is the tool name; `arguments` is the
    parsed JSON object the model produced as arguments.
    """

    name: str
    arguments: Dict[str, Any]
    call_id: Optional[str] = None


@dataclass(frozen=True)
class ToolCallResponse:
    """What a tool-aware adapter returns from one call.

    `content`: the assistant's text response. May be empty when
    `tool_calls` is non-empty — some providers emit only tool calls
    when they decide tool use is the right next step.

    `tool_calls`: parsed tool invocations the model wants the caller to
    dispatch. Empty list means "no tool calls requested."

    `stop_reason`: provider-normalized reason the response stopped.
    "end_turn" / "tool_use" / "max_tokens" / "stop_sequence" / "refusal".
    Adapters map their native reasons to this set.

    `raw`: provider-specific raw response object, for debugging /
    advanced consumers that want richer detail than the normalized shape
    surfaces. Don't rely on its structure across providers.
    """

    content: str
    tool_calls: List[ToolCall] = field(default_factory=list)
    stop_reason: StopReason = "end_turn"
    raw: Optional[Any] = None


class ToolAwareProvider(ABC):
    """Single-shot tool-aware LLM provider.

    The abstraction is one call: messages + (tools | schema) -> response.
    The agent loop (call, dispatch tools, accumulate, call again) is the
    caller's responsibility — stenota's `LensAgent` does this today and
    is the reference consumer.

    Messages follow the OpenAI / Anthropic / Ollama-compatible shape:

        [
          {"role": "system", "content": "..."},
          {"role": "user", "content": "..."},
          {"role": "assistant", "content": "...", "tool_calls": [...]},
          {"role": "tool", "name": "...", "content": "...",
           "tool_call_id": "..."},
          ...
        ]

    Adapters MAY translate this into their native format but MUST
    preserve message ordering and tool-call correlation.

    `tools` and `response_schema` are mutually exclusive on most
    providers — don't pass both at once. When neither is provided, the
    call is a plain chat completion.
    """

    @abstractmethod
    async def generate_with_tools(
        self,
        messages: List[Dict[str, Any]],
        *,
        tools: Optional[List[ToolSchema]] = None,
        response_schema: Optional[Dict[str, Any]] = None,
        model: str,
        temperature: float = 0.2,
        max_tokens: int = 4_096,
    ) -> ToolCallResponse:
        ...

    @property
    def supports_tool_use(self) -> bool:
        """True iff this provider can invoke `tools` argument. Override
        on adapters that don't (yet) support tool use; agents can fall
        back to schema-constrained or plain chat."""
        return True

    @property
    def supports_response_schema(self) -> bool:
        """True iff this provider can constrain output to a JSON schema."""
        return True


# --- Mock implementation --------------------------------------------------


class MockToolProvider(ToolAwareProvider):
    """Test-only provider. Returns scripted responses or canned tool calls.

    Two usage modes:

    1. **Scripted text**: pass `responses=["hello", "world"]`; each call
       pops the next one as `content`.
    2. **Scripted tool calls**: pass `tool_call_scripts=[[...], []]`;
       each call emits the next list of ToolCall objects.

    When both lists are provided, the i-th call returns the i-th text
    AND the i-th tool calls. When a list runs out, calls fall back to
    `default_content`.

    Used by `test_providers_tools.py` for protocol validation. Stenota
    will need a similar mock for its LensAgent tests once it migrates.
    """

    def __init__(
        self,
        responses: Optional[List[str]] = None,
        tool_call_scripts: Optional[List[List[ToolCall]]] = None,
        default_content: str = "",
        supports_tool_use: bool = True,
        supports_response_schema: bool = True,
    ) -> None:
        self._responses = list(responses or [])
        self._tool_call_scripts = list(tool_call_scripts or [])
        self._default_content = default_content
        self._supports_tool_use = supports_tool_use
        self._supports_response_schema = supports_response_schema
        self.call_log: List[Dict[str, Any]] = []

    async def generate_with_tools(
        self,
        messages: List[Dict[str, Any]],
        *,
        tools: Optional[List[ToolSchema]] = None,
        response_schema: Optional[Dict[str, Any]] = None,
        model: str,
        temperature: float = 0.2,
        max_tokens: int = 4_096,
    ) -> ToolCallResponse:
        self.call_log.append(
            {
                "messages": list(messages),
                "tools": list(tools or []),
                "response_schema": response_schema,
                "model": model,
                "temperature": temperature,
                "max_tokens": max_tokens,
            }
        )
        idx = len(self.call_log) - 1
        content = (
            self._responses[idx]
            if idx < len(self._responses)
            else self._default_content
        )
        tool_calls: List[ToolCall] = (
            self._tool_call_scripts[idx]
            if idx < len(self._tool_call_scripts)
            else []
        )
        stop_reason: StopReason = "tool_use" if tool_calls else "end_turn"
        return ToolCallResponse(
            content=content,
            tool_calls=tool_calls,
            stop_reason=stop_reason,
            raw={"mock": True, "call_index": idx},
        )

    @property
    def supports_tool_use(self) -> bool:
        return self._supports_tool_use

    @property
    def supports_response_schema(self) -> bool:
        return self._supports_response_schema


__all__ = [
    "MockToolProvider",
    "StopReason",
    "ToolAwareProvider",
    "ToolCall",
    "ToolCallResponse",
    "ToolSchema",
]
