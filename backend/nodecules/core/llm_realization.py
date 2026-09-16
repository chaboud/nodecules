"""An LLM as a realization: any `ToolAwareProvider` becomes something the
generator can cook with.

The graph names the realization by handle; which provider stands behind
that handle is the inventory's business, never the graph's (hard
invariant 5: no provider names in orchestration). Inputs arrive by role —
`messages`, a list in the provider's message shape (a strip of turns is
the usual source) — and params carry `system`, `model`, `temperature`,
`max_tokens`, and optional `tools` / `response_schema`. The output is a
JSON dict: `content`, `tool_calls`, `stop_reason`, `model`.

A node with no `messages` input can still be considered by a model: every
other input is rendered, by role, as JSON in one user message, so a
decision, a claim, or a table can be put to a model without a chat in
front of it. That is the shape of a deferred `consider/...` step
(core/deferral.py) when a machine with a model picks it up.

Unseeded model calls are perturbing (ADR-0009): `deterministic` defaults
to False, so their receipts say `equivalent`, and a re-production that
happens to match does not get promoted to `exact`.
"""

from __future__ import annotations

import json
from dataclasses import asdict, is_dataclass
from typing import Any, Dict, List, Optional

from .generation import Realization
from .llm_providers import ToolAwareProvider, ToolSchema


def _plain(obj: Any) -> Any:
    if is_dataclass(obj):
        return asdict(obj)
    if hasattr(obj, "model_dump"):
        return obj.model_dump(mode="json")
    return obj


def render_inputs(inputs: Dict[str, Any]) -> str:
    """Inputs by role as one user message: a heading per role and the
    data as JSON. Deterministic (sorted keys) so the same inputs render
    the same prompt."""
    parts: List[str] = []
    for role in sorted(inputs):
        if role == "messages":
            continue
        parts.append(f"## {role}\n{json.dumps(_plain(inputs[role]), sort_keys=True, indent=1, default=str)}")
    return "\n\n".join(parts) if parts else "(no inputs)"


def llm_realization(
    provider: ToolAwareProvider,
    handle: str,
    *,
    deterministic: bool = False,
    default_model: str = "default",
) -> Realization:
    """Wrap a provider as a realization named `handle`."""

    async def cook(inputs: Dict[str, Any], params: Dict[str, Any]) -> Any:
        messages: List[Dict[str, Any]] = list(inputs.get("messages") or [])
        if not messages:
            messages = [{"role": "user", "content": render_inputs(inputs)}]
        system: Optional[str] = params.get("system")
        if system:
            messages = [{"role": "system", "content": system}, *messages]
        tools_raw = params.get("tools")
        tools = [t if isinstance(t, ToolSchema) else ToolSchema(**t) for t in tools_raw] if tools_raw else None
        model = params.get("model", default_model)
        response = await provider.generate_with_tools(
            messages,
            tools=tools,
            response_schema=params.get("response_schema"),
            model=model,
            temperature=float(params.get("temperature", 0.2)),
            max_tokens=int(params.get("max_tokens", 4_096)),
        )
        return {
            "content": response.content,
            "tool_calls": [_plain(tc) for tc in response.tool_calls],
            "stop_reason": response.stop_reason,
            "model": model,
        }

    return Realization(handle=handle, cook=cook, deterministic=deterministic)


__all__ = ["llm_realization", "render_inputs"]
