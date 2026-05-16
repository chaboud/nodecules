"""Tests for PR-r1 — the typed strip-access ADT.

Grounded against the nine real stenota nodes (decode, asr, diar, turns,
summarize, finalize, speaker_relabel, participation, llm_tool_loop). The
ADT carries exactly the access shapes those nodes exhibit:

- `AllPattern`    — whole-strip read (turns, participation, speaker_relabel)
- `LatestPattern` — single most-recent / single-blob read (asr, diar read
                    audio.wav)
- `RangePattern`  — windowed read with a temporal-field selector
                    (summarize: turns whose `source_window` intersects the
                    active window)

Speculative IIR / ordinal patterns (Before/After/OrdinalAt/SelfRelative)
are intentionally NOT in the ADT — no current node uses them. They get
added when a real streaming node needs them.
"""

from __future__ import annotations

import pytest
from pydantic import TypeAdapter, ValidationError

from nodecules.core.strip_access import (
    AbsoluteMs,
    AccessPattern,
    AllPattern,
    LatestPattern,
    RangePattern,
    SelfWindowEnd,
    SelfWindowStart,
    StripAccess,
    TimeExpr,
)


_TIME_EXPR = TypeAdapter(TimeExpr)
_ACCESS_PATTERN = TypeAdapter(AccessPattern)


class TestTimeExprConstruction:
    def test_self_window_start_constructs(self) -> None:
        assert SelfWindowStart().kind == "self_window_start"

    def test_self_window_end_constructs(self) -> None:
        assert SelfWindowEnd().kind == "self_window_end"

    def test_absolute_ms_constructs(self) -> None:
        assert AbsoluteMs(ms=90_000).ms == 90_000

    def test_absolute_ms_zero_is_valid(self) -> None:
        assert AbsoluteMs(ms=0).ms == 0

    def test_absolute_ms_negative_rejected(self) -> None:
        # Time is non-negative integer ms, meeting-relative (CLAUDE.md).
        with pytest.raises(ValidationError):
            AbsoluteMs(ms=-1)


class TestTimeExprRoundTrip:
    def test_self_window_start_round_trips(self) -> None:
        expr = SelfWindowStart()
        restored = _TIME_EXPR.validate_json(_TIME_EXPR.dump_json(expr))
        assert restored == expr

    def test_self_window_end_round_trips(self) -> None:
        expr = SelfWindowEnd()
        restored = _TIME_EXPR.validate_json(_TIME_EXPR.dump_json(expr))
        assert restored == expr

    def test_absolute_ms_round_trips(self) -> None:
        expr = AbsoluteMs(ms=90_000)
        restored = _TIME_EXPR.validate_json(_TIME_EXPR.dump_json(expr))
        assert restored == expr

    def test_discriminator_selects_correct_variant(self) -> None:
        # A bare TimeExpr deserializes to the right concrete type via the
        # `kind` discriminator — no ambiguity between the three variants.
        restored = _TIME_EXPR.validate_json('{"kind": "absolute_ms", "ms": 5}')
        assert isinstance(restored, AbsoluteMs)
        assert restored.ms == 5


class TestAccessPatternConstruction:
    def test_all_pattern_constructs(self) -> None:
        # Whole-strip read — turns, participation, speaker_relabel.
        assert AllPattern().kind == "all"

    def test_latest_pattern_constructs(self) -> None:
        # Single-element read — asr/diar read audio.wav as one blob.
        assert LatestPattern().kind == "latest"

    def test_range_pattern_constructs(self) -> None:
        # Windowed read — summarize reads turns whose `source_window`
        # intersects the active window.
        rp = RangePattern(
            field="source_window",
            start=SelfWindowStart(),
            end=SelfWindowEnd(),
        )
        assert rp.kind == "range"
        assert rp.field == "source_window"
        assert isinstance(rp.start, SelfWindowStart)
        assert isinstance(rp.end, SelfWindowEnd)

    def test_range_pattern_empty_field_rejected(self) -> None:
        # The field selector names which temporal attribute of the target
        # events the window tests — it must be a real field name.
        with pytest.raises(ValidationError):
            RangePattern(field="", start=SelfWindowStart(), end=SelfWindowEnd())

    def test_range_pattern_accepts_absolute_endpoints(self) -> None:
        rp = RangePattern(
            field="time_ranges",
            start=AbsoluteMs(ms=0),
            end=AbsoluteMs(ms=90_000),
        )
        assert isinstance(rp.start, AbsoluteMs)
        assert rp.end.ms == 90_000


class TestAccessPatternRoundTrip:
    def test_all_pattern_round_trips(self) -> None:
        p = AllPattern()
        assert _ACCESS_PATTERN.validate_json(_ACCESS_PATTERN.dump_json(p)) == p

    def test_latest_pattern_round_trips(self) -> None:
        p = LatestPattern()
        assert _ACCESS_PATTERN.validate_json(_ACCESS_PATTERN.dump_json(p)) == p

    def test_range_pattern_round_trips(self) -> None:
        p = RangePattern(
            field="source_window",
            start=SelfWindowStart(),
            end=SelfWindowEnd(),
        )
        assert _ACCESS_PATTERN.validate_json(_ACCESS_PATTERN.dump_json(p)) == p

    def test_discriminator_selects_correct_pattern(self) -> None:
        restored = _ACCESS_PATTERN.validate_json('{"kind": "all"}')
        assert isinstance(restored, AllPattern)


class TestStripAccess:
    def test_wraps_strip_name_and_pattern(self) -> None:
        # SummarizerL2's real declaration: read the turns strip,
        # windowed by source_window.
        sa = StripAccess(
            strip_name="strips/turns/diarized",
            pattern=RangePattern(
                field="source_window",
                start=SelfWindowStart(),
                end=SelfWindowEnd(),
            ),
        )
        assert sa.strip_name == "strips/turns/diarized"
        assert isinstance(sa.pattern, RangePattern)

    def test_accepts_all_pattern(self) -> None:
        # TurnsL2's real declaration: whole-strip read of ASR segments.
        sa = StripAccess(strip_name="strips/asr/segments", pattern=AllPattern())
        assert isinstance(sa.pattern, AllPattern)

    def test_empty_strip_name_rejected(self) -> None:
        with pytest.raises(ValidationError):
            StripAccess(strip_name="", pattern=AllPattern())

    def test_round_trips_through_json(self) -> None:
        sa = StripAccess(
            strip_name="strips/turns/diarized",
            pattern=RangePattern(
                field="source_window",
                start=SelfWindowStart(),
                end=SelfWindowEnd(),
            ),
        )
        restored = StripAccess.model_validate_json(sa.model_dump_json())
        assert restored == sa

    def test_pattern_discriminator_survives_nested_round_trip(self) -> None:
        # The pattern's concrete type is recovered through the StripAccess
        # envelope, not just when deserialized bare.
        sa = StripAccess(strip_name="strips/raw/audio", pattern=LatestPattern())
        restored = StripAccess.model_validate_json(sa.model_dump_json())
        assert isinstance(restored.pattern, LatestPattern)


class TestNodeSpecIntegration:
    """`NodeSpec.reads_strip_patterns` must be additive — existing nodes
    construct unchanged, and the new field defaults to empty."""

    def test_existing_nodespec_construction_unaffected(self) -> None:
        from nodecules.core.types import NodeSpec

        spec = NodeSpec(
            node_type="x.test",
            display_name="Test",
            description="A node declared with no strip patterns.",
        )
        assert spec.reads_strip_patterns == []

    def test_nodespec_carries_declared_patterns(self) -> None:
        from nodecules.core.types import NodeSpec

        spec = NodeSpec(
            node_type="stenota.summarizer_l2",
            display_name="L2 Summarizer",
            description="Windowed claim extraction.",
            reads_strip_patterns=[
                StripAccess(
                    strip_name="strips/turns/diarized",
                    pattern=RangePattern(
                        field="source_window",
                        start=SelfWindowStart(),
                        end=SelfWindowEnd(),
                    ),
                )
            ],
        )
        assert len(spec.reads_strip_patterns) == 1
        assert spec.reads_strip_patterns[0].strip_name == "strips/turns/diarized"
