"""Shared pieces for the demos. Not core."""

from __future__ import annotations

from typing import Any, Dict, List, Optional

from nodecules.core.generation import Generation
from nodecules.core.llm_providers import ToolAwareProvider, ToolCallResponse, ToolSchema
from nodecules.core.scene import Frame


class EchoProvider(ToolAwareProvider):
    """A stand-in model that answers deterministically from the last user
    message and the system prompt, so a demo runs anywhere and the effect
    of editing the recipe is visible. Replace with a real provider to talk
    to a model."""

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
        system = next((m["content"] for m in messages if m["role"] == "system"), "")
        last_user = next((m["content"] for m in reversed(messages) if m["role"] == "user"), "")
        turns = sum(1 for m in messages if m["role"] == "user")
        voice = "Arr" if "pirate" in system.lower() else "Well"
        content = f"{voice}, on turn {turns} you said {last_user!r}. ({system.strip() or 'no system prompt'})"
        return ToolCallResponse(content=content, tool_calls=[], stop_reason="end_turn", raw={"echo": True})

    @property
    def supports_tool_use(self) -> bool:
        return False

    @property
    def supports_response_schema(self) -> bool:
        return False


def receipt_line(g: Generation) -> str:
    """One line per production: what happened and why."""
    how = "cache hit" if g.cache_hit else ("cooked" if g.cooked else g.outcome)
    key = (g.cache_key or "")[:10]
    return (
        f"  receipt  {g.node_id}: {how} · outcome={g.outcome} · reproducibility={g.reproducibility}"
        f"{' (measured)' if g.measured else ''} · realization={g.realization} · key={key}"
    )


def text_frame(fr: Frame) -> str:
    """A presenter that interprets a frame as lines of text — the simplest
    surface, and a reminder that a frame is semantics, not pixels."""
    lines = [f"frame @ {fr.t} on {fr.timeline} ({len(fr.elements)} elements)"]
    for el in fr.elements:
        hint = f" [{el.enter.kind} {el.enter.duration}]" if el.enter else ""
        lines.append(f"  {el.region or '-'} z{el.z} {el.id}: {el.payload!r}{hint}")
    return "\n".join(lines)
