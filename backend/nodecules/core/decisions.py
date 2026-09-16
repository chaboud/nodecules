"""Decisions and their presentations — the choice-architecture half of the
table (founder, 2026-09-11: "it's really about choice architecture";
business-level and interaction-level transitions are not 1:1).

A **decision** is a business-level node: what must be chosen, the options,
the facts each option must show, the stakes, a deadline. It says nothing
about screens.

A **presentation** is an interaction-level node produced *from* the
decision for one surface and one person: a realization reads the decision
and the answers given so far and derives the current step. Sixteen
options on a phone that shows four become a tournament of rounds; the same
sixteen on a wall display are one step; for a person who does not see,
the step carries a spoken form. Different realizations, the same decision,
the same outcome.

The presentation node is a pure function of (decision, answers, params),
so it recooks whenever an answer lands on the input strip and never holds
hidden state. When the last answer is in, it says `done` and names the
choice; a business node that reads it advances exactly once, because its
cache key changes exactly once.

Every step is an element for the table (`step_element`): a payload that
carries the choice structure and never pixels, so any presenter, screen
reader included, can interpret it.
"""

from __future__ import annotations

from typing import Any, Dict, List, Optional, Sequence, Tuple

from pydantic import BaseModel, ConfigDict, Field

from .generation import Realization
from .scene import Element, Transition

DECISION_KIND = "decision"
PRESENTATION_KIND = "presentation"
OUTCOME_KIND = "decision.outcome"


class Option(BaseModel):
    model_config = ConfigDict(frozen=True)

    id: str
    label: str
    facts: Dict[str, Any] = Field(default_factory=dict)


class Decision(BaseModel):
    """What must be decided, at the business level."""

    model_config = ConfigDict(frozen=True)

    id: str
    prompt: str
    options: Tuple[Option, ...]
    needs: Tuple[str, ...] = ()  # fact fields every option must show before a person can judge it
    stakes: str = ""
    deadline: Optional[int] = None  # ticks on the table's timeline

    def missing_facts(self) -> Dict[str, List[str]]:
        """Options that do not show a needed fact — the choice architecture
        cannot be fair if the information is not there."""
        out: Dict[str, List[str]] = {}
        for o in self.options:
            lacking = [n for n in self.needs if n not in o.facts]
            if lacking:
                out[o.id] = lacking
        return out


class Step(BaseModel):
    """One interaction step: which options to show and what to ask."""

    model_config = ConfigDict(frozen=True)

    id: str
    round: int
    options: Tuple[Option, ...]
    ask: str
    of: int  # steps in this round
    index: int


class Presentation(BaseModel):
    """The interaction-level state for one surface and person."""

    model_config = ConfigDict(frozen=True)

    decision: str
    mode: str  # "all-at-once" | "tournament"
    step: Optional[Step]
    done: bool
    chosen: Optional[str] = None
    path: Tuple[Dict[str, Any], ...] = ()
    rejected_answers: Tuple[Dict[str, Any], ...] = ()
    modality: str = "visual"
    spoken: Optional[str] = None


def _chunks(items: Sequence[Option], size: int) -> List[Tuple[Option, ...]]:
    return [tuple(items[i : i + size]) for i in range(0, len(items), size)]


def derive(decision: Decision, answers: Sequence[Dict[str, Any]], *, max_options: int, non_visual: bool = False) -> Presentation:
    """The current step of presenting `decision` to someone who can see at
    most `max_options` at once, given the answers so far. Deterministic."""
    max_options = max(2, int(max_options))
    by_id = {o.id: o for o in decision.options}
    valid = [a for a in answers if isinstance(a, dict) and a.get("chosen") in by_id]
    mode = "all-at-once" if len(decision.options) <= max_options else "tournament"
    pool: List[Option] = list(decision.options)
    round_no = 1
    path: List[Dict[str, Any]] = []
    rejected: List[Dict[str, Any]] = []
    cursor = 0
    while True:
        groups = _chunks(pool, max_options)
        if len(groups) == 1 and (round_no > 1 or mode == "all-at-once" or len(pool) <= max_options):
            step_id = "final" if mode == "tournament" else "choose"
            step = Step(id=step_id, round=round_no, options=groups[0], ask=decision.prompt, of=1, index=0)
            if cursor < len(valid) and valid[cursor].get("step") == step_id:
                a = valid[cursor]
                path.append({"step": step_id, "chosen": a["chosen"]})
                return _finish(decision, mode, path, rejected, non_visual, a["chosen"])
            rejected.extend(valid[cursor + 1 :])
            return _present(decision, mode, step, path, rejected, non_visual)
        winners: List[Option] = []
        for gi, group in enumerate(groups):
            step_id = f"r{round_no}g{gi}"
            if cursor < len(valid) and valid[cursor].get("step") == step_id and valid[cursor]["chosen"] in {o.id for o in group}:
                a = valid[cursor]
                winners.append(by_id[a["chosen"]])
                path.append({"step": step_id, "chosen": a["chosen"]})
                cursor += 1
                continue
            if cursor < len(valid):
                rejected.append(valid[cursor])  # an answer for a step that is not current
                cursor += 1
            step = Step(id=step_id, round=round_no, options=group, ask=f"{decision.prompt} (round {round_no}, group {gi + 1} of {len(groups)})", of=len(groups), index=gi)
            return _present(decision, mode, step, path, rejected, non_visual)
        pool = winners
        round_no += 1


def _spoken(step: Step) -> str:
    parts = [f"{i + 1} for {o.label}" for i, o in enumerate(step.options)]
    return f"{step.ask}. Say " + ", ".join(parts) + "."


def _present(decision: Decision, mode: str, step: Step, path, rejected, non_visual: bool) -> Presentation:
    return Presentation(
        decision=decision.id,
        mode=mode,
        step=step,
        done=False,
        path=tuple(path),
        rejected_answers=tuple(rejected),
        modality="audio" if non_visual else "visual",
        spoken=_spoken(step) if non_visual else None,
    )


def _finish(decision: Decision, mode: str, path, rejected, non_visual: bool, chosen: str) -> Presentation:
    return Presentation(decision=decision.id, mode=mode, step=None, done=True, chosen=chosen, path=tuple(path), rejected_answers=tuple(rejected), modality="audio" if non_visual else "visual")


def presentation_realization(handle: str = "present.choice@1") -> Realization:
    """The realization behind a presentation node. Inputs by role: `decision`
    (the decision node's data) and, optionally, `answers` (the person's input
    strip). Params: `max_options` (the surface's capacity), `non_visual`."""

    async def cook(inputs: Dict[str, Any], params: Dict[str, Any]) -> Any:
        decision = Decision(**inputs["decision"])
        answers = list(inputs.get("answers") or [])
        state = derive(decision, answers, max_options=int(params.get("max_options", 8)), non_visual=bool(params.get("non_visual", False)))
        return state.model_dump(mode="json")

    return Realization(handle=handle, cook=cook, deterministic=True)


def advance_realization(handle: str = "decision.advance@1") -> Realization:
    """The business node's realization: reads a presentation and advances
    only when it is done. Its cache key changes exactly once, when `done`
    flips, so the business graph moves exactly one step."""

    async def cook(inputs: Dict[str, Any], params: Dict[str, Any]) -> Any:
        p = inputs["choice"]
        if not p.get("done"):
            return {"advanced": False, "waiting_on": p.get("step", {}).get("id") if p.get("step") else None}
        return {"advanced": True, "chosen": p["chosen"], "decision": p["decision"], "path": p.get("path", [])}

    return Realization(handle=handle, cook=cook, deterministic=True)


def step_element(p: Presentation, *, present_at: int, expires_at: int, region: str = "main", enter: Optional[Transition] = None) -> Optional[Element]:
    """The current step as an element for the table: choice structure and
    facts, a spoken form when the person does not see, never pixels."""
    if p.step is None:
        return None
    payload = {
        "kind": "choice",
        "decision": p.decision,
        "step": p.step.id,
        "ask": p.step.ask,
        "options": [o.model_dump(mode="json") for o in p.step.options],
        "progress": {"round": p.step.round, "index": p.step.index, "of": p.step.of, "mode": p.mode},
        "modality": p.modality,
    }
    if p.spoken:
        payload["spoken"] = p.spoken
    return Element(present_at=present_at, expires_at=expires_at, region=region, payload=payload, enter=enter)


__all__ = [
    "DECISION_KIND",
    "OUTCOME_KIND",
    "PRESENTATION_KIND",
    "Decision",
    "Option",
    "Presentation",
    "Step",
    "advance_realization",
    "derive",
    "presentation_realization",
    "step_element",
]
