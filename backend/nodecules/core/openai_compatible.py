"""A tool-aware provider over any OpenAI-compatible chat endpoint, standard
library only. vLLM, llama.cpp's server, Ollama's OpenAI route, LM Studio,
and the hosted APIs all speak this shape, which is why the DGX Spark can
stand behind the same interface as the mock.

The graph never names this class (hard invariant 5); an inventory does,
by wrapping it as a realization with `llm_realization`.

Verified here only against a fake server (see the test); the first real
model behind it is the Spark's job, per `handoff/SPARK.md`.
"""

from __future__ import annotations

import json
import urllib.error
import urllib.request
from typing import Any, Dict, List, Optional

from .llm_providers import StopReason, ToolAwareProvider, ToolCall, ToolCallResponse, ToolSchema


class OpenAICompatibleProvider(ToolAwareProvider):
    def __init__(
        self,
        base_url: str,
        *,
        api_key: str = "",
        timeout_s: float = 120.0,
        extra_headers: Optional[Dict[str, str]] = None,
        extra_body: Optional[Dict[str, Any]] = None,
    ) -> None:
        self.base_url = base_url.rstrip("/")
        self.api_key = api_key
        self.timeout_s = timeout_s
        self.extra_headers = dict(extra_headers or {})
        # Engine-specific request fields merged into every payload, e.g.
        # llama.cpp's {"chat_template_kwargs": {"enable_thinking": false}}.
        # Found on the Spark, 2026-09-19: a thinking model (Qwen3.8-27B) spent
        # the recipe's whole max_tokens on `reasoning_content` and returned
        # content "" with finish "length" — the first two answers were empty.
        # The graph never sees this; it is operator configuration, like the
        # endpoint itself.
        self.extra_body = dict(extra_body or {})
        self.last_raw: Optional[Dict[str, Any]] = None

    def _post(self, path: str, payload: Dict[str, Any]) -> Dict[str, Any]:
        data = json.dumps(payload).encode("utf-8")
        req = urllib.request.Request(self.base_url + path, data=data, method="POST")
        req.add_header("Content-Type", "application/json")
        if self.api_key:
            req.add_header("Authorization", f"Bearer {self.api_key}")
        for k, v in self.extra_headers.items():
            req.add_header(k, v)
        with urllib.request.urlopen(req, timeout=self.timeout_s) as resp:  # noqa: S310 - the URL is the operator's own endpoint
            return json.loads(resp.read().decode("utf-8"))

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
        payload: Dict[str, Any] = {"model": model, "messages": messages, "temperature": temperature, "max_tokens": max_tokens}
        payload.update(self.extra_body)
        if tools:
            payload["tools"] = [{"type": "function", "function": {"name": t.name, "description": getattr(t, "description", ""), "parameters": getattr(t, "parameters", {}) or {}}} for t in tools]
        if response_schema:
            payload["response_format"] = {"type": "json_schema", "json_schema": {"name": "response", "schema": response_schema}}
        raw = self._post("/v1/chat/completions", payload)
        self.last_raw = raw
        choice = (raw.get("choices") or [{}])[0]
        msg = choice.get("message") or {}
        calls: List[ToolCall] = []
        for tc in msg.get("tool_calls") or []:
            fn = tc.get("function") or {}
            args = fn.get("arguments")
            if isinstance(args, str):
                try:
                    args = json.loads(args)
                except json.JSONDecodeError:
                    args = {"_raw": args}
            calls.append(ToolCall(call_id=tc.get("id") or "", name=fn.get("name") or "", arguments=args or {}))
        finish = choice.get("finish_reason") or "stop"
        stop: StopReason = "tool_use" if calls else ("max_tokens" if finish == "length" else "end_turn")
        return ToolCallResponse(content=msg.get("content") or "", tool_calls=calls, stop_reason=stop, raw=raw)

    @property
    def supports_tool_use(self) -> bool:
        return True

    @property
    def supports_response_schema(self) -> bool:
        return True


__all__ = ["OpenAICompatibleProvider"]
