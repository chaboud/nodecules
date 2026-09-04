"""Tests for PR-d1 — descriptions and the satisfies judgment.

Grounded on the first two real contracts, drafted from stenota's actual
ASR and diarization nodes (`stenota.asr_faster_whisper`,
`stenota.diar_pyannote`): they read `audio.wav` as a single blob and write
one segment strip each. The fixtures mirror those NodeSpecs field for
field; stenota's own test suite checks the real objects against the same
descriptions.
"""

from __future__ import annotations

import json

import pytest
from pydantic import ValidationError

from nodecules.core.assay_metrics import der, max_abs, register_metric, score, wer
from nodecules.core.descriptions import (
    AssayResult,
    Description,
    Hallmark,
    ProducedStrip,
    SatisfiesClaim,
    StripRequirement,
    Tolerance,
    assert_functional,
    decide,
    run_assay,
    valence_check,
    verify_hallmark,
)
from nodecules.core.strip_access import (
    AllPattern,
    LatestPattern,
    RangePattern,
    SelfWindowEnd,
    SelfWindowStart,
    StripAccess,
)
from nodecules.core.types import NodeSpec

# --- Fixtures: the two real contracts, and specs shaped like stenota's ----

AUDIO = "audio.wav"
ASR_STRIP = "strips/asr/segments"
DIAR_STRIP = "strips/diar/segments"

ASR_V1 = Description(
    name="asr/v1",
    consumes=(StripRequirement(strip_name=AUDIO, pattern=LatestPattern()),),
    produces=(ProducedStrip(strip_name=ASR_STRIP, schema_id="stenota.core.models.ASRSegment"),),
    tolerance=Tolerance(metric="wer", max_value=0.15),
    reference="stenota.asr_faster_whisper@0.1.0",
)

DIARIZE_V1 = Description(
    name="diarize/v1",
    consumes=(StripRequirement(strip_name=AUDIO, pattern=LatestPattern()),),
    produces=(ProducedStrip(strip_name=DIAR_STRIP, schema_id="stenota.core.models.DiarSegment"),),
    tolerance=Tolerance(metric="der", max_value=0.20),
    reference="stenota.diar_pyannote@0.1.0",
)


def _spec(node_type: str, reads, writes, patterns=True) -> NodeSpec:
    return NodeSpec(
        node_type=node_type,
        display_name=node_type,
        description="fixture",
        reads_strips=[name for name, _ in reads],
        reads_strip_patterns=[
            StripAccess(strip_name=name, pattern=p) for name, p in reads
        ] if patterns else [],
        writes_strips=list(writes),
    )


ASR_SPEC = _spec("stenota.asr_faster_whisper@0.1.0", [(AUDIO, LatestPattern())], [ASR_STRIP])
DIAR_SPEC = _spec("stenota.diar_pyannote@0.1.0", [(AUDIO, LatestPattern())], [DIAR_STRIP])


# --- Identity ---------------------------------------------------------------


class TestDescriptionIdentity:
    def test_hash_is_stable(self) -> None:
        assert ASR_V1.content_hash() == ASR_V1.content_hash()

    def test_name_is_an_alias_not_identity(self) -> None:
        renamed = ASR_V1.model_copy(update={"name": "speech-to-text/v1"})
        assert renamed.content_hash() == ASR_V1.content_hash()

    def test_tolerance_is_identity(self) -> None:
        looser = ASR_V1.model_copy(update={"tolerance": Tolerance(metric="wer", max_value=0.30)})
        assert looser.content_hash() != ASR_V1.content_hash()

    def test_reference_is_identity(self) -> None:
        other = ASR_V1.model_copy(update={"reference": "someone-elses-asr@1.0.0"})
        assert other.content_hash() != ASR_V1.content_hash()

    def test_round_trips_through_json(self) -> None:
        restored = Description.model_validate_json(ASR_V1.model_dump_json())
        assert restored == ASR_V1
        assert restored.content_hash() == ASR_V1.content_hash()

    def test_must_produce_something(self) -> None:
        with pytest.raises(ValidationError):
            Description(
                name="x", consumes=(), produces=(),
                tolerance=Tolerance(metric="wer", max_value=0.1), reference="r",
            )


# --- The valence check --------------------------------------------------------


class TestValenceCheck:
    def test_real_shaped_asr_spec_passes(self) -> None:
        assert valence_check(ASR_SPEC, ASR_V1) == []

    def test_real_shaped_diar_spec_passes(self) -> None:
        assert valence_check(DIAR_SPEC, DIARIZE_V1) == []

    def test_asr_does_not_satisfy_diarize(self) -> None:
        problems = valence_check(ASR_SPEC, DIARIZE_V1)
        assert any(DIAR_STRIP in p for p in problems)

    def test_undeclared_access_is_not_checkable(self) -> None:
        bare = _spec("bare", [(AUDIO, LatestPattern())], [ASR_STRIP], patterns=False)
        problems = valence_check(bare, ASR_V1)
        assert any("not statically checkable" in p for p in problems)

    def test_wrong_pattern_kind_fails(self) -> None:
        whole = _spec("whole", [(AUDIO, AllPattern())], [ASR_STRIP])
        problems = valence_check(whole, ASR_V1)
        assert any("requires 'latest'" in p for p in problems)

    def test_missing_output_fails(self) -> None:
        silent = _spec("silent", [(AUDIO, LatestPattern())], [])
        problems = valence_check(silent, ASR_V1)
        assert problems == [f"silent does not write {ASR_STRIP!r}"]

    def test_range_field_must_match(self) -> None:
        desc = Description(
            name="summarize/v1",
            consumes=(StripRequirement(
                strip_name="strips/turns/diarized",
                pattern=RangePattern(field="source_window", start=SelfWindowStart(), end=SelfWindowEnd()),
            ),),
            produces=(ProducedStrip(strip_name="claims/L2", schema_id="StructuredClaim"),),
            tolerance=Tolerance(metric="max_abs", max_value=0.0),
            reference="ref",
        )
        right = _spec("r", [("strips/turns/diarized", RangePattern(
            field="source_window", start=SelfWindowStart(), end=SelfWindowEnd()))], ["claims/L2"])
        wrong = _spec("w", [("strips/turns/diarized", RangePattern(
            field="time_ranges", start=SelfWindowStart(), end=SelfWindowEnd()))], ["claims/L2"])
        assert valence_check(right, desc) == []
        assert valence_check(wrong, desc) != []


# --- Decoration direction ----------------------------------------------------


class TestDecorationDirection:
    def test_functional_specs_pass(self) -> None:
        assert_functional(ASR_SPEC)
        assert_functional(DIAR_SPEC)

    def test_reading_hallmarks_is_rejected(self) -> None:
        bad = _spec("reputation-aware-asr", [(AUDIO, LatestPattern()), ("hallmarks/asr", AllPattern())], [ASR_STRIP])
        with pytest.raises(ValueError, match="decoration"):
            assert_functional(bad)

    def test_writing_claims_is_rejected(self) -> None:
        bad = _spec("self-vouching", [(AUDIO, LatestPattern())], [ASR_STRIP, "claims/asr"])
        with pytest.raises(ValueError, match="decoration"):
            assert_functional(bad)

    def test_claim_cites_without_being_cited(self) -> None:
        # A claim references the realization and the description; the
        # realization's spec is untouched by its existence.
        before = ASR_SPEC.reads_strips + ASR_SPEC.writes_strips
        claim = SatisfiesClaim(
            realization=ASR_SPEC.node_type,
            description_hash=ASR_V1.content_hash(),
            claimant="stenota", grade="reference", cost_class="local-gpu",
        )
        assert claim.description_hash == ASR_V1.content_hash()
        assert ASR_SPEC.reads_strips + ASR_SPEC.writes_strips == before


# --- Metrics -----------------------------------------------------------------


class TestMetrics:
    def test_wer_identical_is_zero(self) -> None:
        assert wer("the cat sat".split(), "the cat sat".split()) == 0.0

    def test_wer_one_substitution_in_four(self) -> None:
        assert wer("the cat sat down".split(), "the dog sat down".split()) == 0.25

    def test_wer_insertion_and_deletion(self) -> None:
        assert wer("the cat".split(), "the cat sat".split()) == pytest.approx(1 / 3)
        assert wer("the big cat sat".split(), "the cat sat".split()) == pytest.approx(1 / 3)

    def test_wer_empty_reference(self) -> None:
        assert wer([], []) == 0.0
        assert wer(["noise"], []) == float("inf")

    def test_der_identical_is_zero(self) -> None:
        turns = [(0, 1000, "A"), (1000, 2500, "B"), (2500, 3000, "A")]
        assert der(turns, turns) == 0.0

    def test_der_is_permutation_invariant(self) -> None:
        # Cluster labels are arbitrary: SPEAKER_00 in one run is SPEAKER_03
        # in another. A relabelling must score as identical.
        ref = [(0, 1000, "SPEAKER_00"), (1000, 2500, "SPEAKER_01"), (2500, 3000, "SPEAKER_00")]
        hyp = [(0, 1000, "SPEAKER_03"), (1000, 2500, "SPEAKER_07"), (2500, 3000, "SPEAKER_03")]
        assert der(hyp, ref) == 0.0

    def test_der_missed_speech(self) -> None:
        ref = [(0, 1000, "A"), (1000, 2000, "B")]
        hyp = [(0, 1000, "X")]
        assert der(hyp, ref) == pytest.approx(0.5)

    def test_der_false_alarm(self) -> None:
        ref = [(0, 1000, "A")]
        hyp = [(0, 1000, "X"), (1000, 1500, "X")]
        assert der(hyp, ref) == pytest.approx(0.5)

    def test_der_confusion(self) -> None:
        # Two speakers merged into one cluster: the best mapping matches
        # the longer one; the shorter is confusion.
        ref = [(0, 3000, "A"), (3000, 4000, "B")]
        hyp = [(0, 4000, "X")]
        assert der(hyp, ref) == pytest.approx(0.25)

    def test_max_abs_matches_the_bench(self) -> None:
        assert max_abs([(0.1, 0.2, 0.3)], [(0.1, 0.2, 0.3)]) == 0.0
        assert max_abs([(0.1, 0.2, 0.3)], [(0.1, 0.25, 0.3)]) == pytest.approx(0.05)

    def test_registry_is_open(self) -> None:
        register_metric("always_zero", lambda c, r: 0.0)
        assert score("always_zero", None, None) == 0.0
        with pytest.raises(KeyError):
            score("no-such-metric", None, None)


# --- Assay, decision, receipt ------------------------------------------------

REF_WORDS = "we should ship the store before the scheduler".split()


def _claims():
    return [
        SatisfiesClaim(realization="stenota.asr_faster_whisper@0.1.0",
                       description_hash=ASR_V1.content_hash(), claimant="stenota",
                       grade="reference", cost_class="local-gpu"),
        SatisfiesClaim(realization="tiny-asr@0.3.0",
                       description_hash=ASR_V1.content_hash(), claimant="vendor",
                       grade="fast", cost_class="local-cpu"),
    ]


def _specs():
    return {
        "stenota.asr_faster_whisper@0.1.0": ASR_SPEC,
        "tiny-asr@0.3.0": _spec("tiny-asr@0.3.0", [(AUDIO, LatestPattern())], [ASR_STRIP]),
    }


def _assay(realization: str, words, cost: float) -> AssayResult:
    return run_assay(ASR_V1, realization, words, REF_WORDS,
                     n_probes=1, probe_provenance="fresh-drawn", cost_s=cost)


class TestDecide:
    def test_cheaper_substitute_wins_when_it_passes(self) -> None:
        assays = {
            "stenota.asr_faster_whisper@0.1.0": _assay("stenota.asr_faster_whisper@0.1.0", REF_WORDS, 8.0),
            "tiny-asr@0.3.0": _assay("tiny-asr@0.3.0", "we should ship the store before a scheduler".split(), 1.0),
        }
        b = decide(ASR_V1, _claims(), _specs(), assays)
        assert b.chosen is not None
        assert b.chosen.realization == "tiny-asr@0.3.0"
        assert b.chosen.hallmark.outcome == "via-substitute"
        assert b.rejected == {}

    def test_cheap_but_wrong_is_rejected_before_cost_matters(self) -> None:
        assays = {
            "stenota.asr_faster_whisper@0.1.0": _assay("stenota.asr_faster_whisper@0.1.0", REF_WORDS, 8.0),
            "tiny-asr@0.3.0": _assay("tiny-asr@0.3.0", "we shipped a store".split(), 1.0),
        }
        b = decide(ASR_V1, _claims(), _specs(), assays)
        assert b.chosen is not None
        assert b.chosen.realization == "stenota.asr_faster_whisper@0.1.0"
        assert b.chosen.hallmark.outcome == "exact"
        assert "assay:" in b.rejected["tiny-asr@0.3.0"]

    def test_valence_failure_is_rejected_with_reason(self) -> None:
        specs = _specs()
        specs["tiny-asr@0.3.0"] = _spec("tiny-asr@0.3.0", [(AUDIO, AllPattern())], [ASR_STRIP])
        assays = {"stenota.asr_faster_whisper@0.1.0": _assay("stenota.asr_faster_whisper@0.1.0", REF_WORDS, 8.0),
                  "tiny-asr@0.3.0": _assay("tiny-asr@0.3.0", REF_WORDS, 1.0)}
        b = decide(ASR_V1, _claims(), specs, assays)
        assert b.chosen is not None and b.chosen.realization == "stenota.asr_faster_whisper@0.1.0"
        assert b.rejected["tiny-asr@0.3.0"].startswith("valence:")

    def test_unmeasured_is_not_bound(self) -> None:
        b = decide(ASR_V1, _claims(), _specs(), {})
        assert b.chosen is None
        assert all("no assay result" in why for why in b.rejected.values())

    def test_no_claims_fails_ordinarily(self) -> None:
        b = decide(ASR_V1, [], _specs(), {})
        assert b.chosen is None and b.rejected == {}

    def test_claims_for_other_descriptions_are_ignored(self) -> None:
        other = SatisfiesClaim(realization="stenota.diar_pyannote@0.1.0",
                               description_hash=DIARIZE_V1.content_hash(), claimant="stenota")
        b = decide(ASR_V1, [other], _specs(), {})
        assert b.chosen is None and b.rejected == {}


class TestHallmark:
    def test_outcome_is_derived_from_identity(self) -> None:
        exact = _assay("stenota.asr_faster_whisper@0.1.0", REF_WORDS, 1.0).hallmark
        sub = _assay("tiny-asr@0.3.0", REF_WORDS, 1.0).hallmark
        assert exact.outcome == "exact" and sub.outcome == "via-substitute"

    def test_verify_accepts_honest_receipt(self) -> None:
        h = _assay("tiny-asr@0.3.0", REF_WORDS, 1.0).hallmark
        ok, why = verify_hallmark(h, ASR_V1, remeasured=0.05)
        assert ok, why

    def test_verify_catches_forged_outcome(self) -> None:
        h = _assay("tiny-asr@0.3.0", REF_WORDS, 1.0).hallmark.model_copy(update={"outcome": "exact"})
        ok, why = verify_hallmark(h, ASR_V1, remeasured=0.0)
        assert not ok and "outcome mismatch" in why

    def test_verify_catches_wrong_description(self) -> None:
        h = _assay("tiny-asr@0.3.0", REF_WORDS, 1.0).hallmark
        ok, why = verify_hallmark(h, DIARIZE_V1, remeasured=0.0)
        assert not ok and "different description" in why

    def test_verify_catches_remeasured_failure(self) -> None:
        h = _assay("tiny-asr@0.3.0", REF_WORDS, 1.0).hallmark
        ok, why = verify_hallmark(h, ASR_V1, remeasured=0.40)
        assert not ok and "exceeds" in why

    def test_provenance_is_required_and_closed(self) -> None:
        with pytest.raises(ValidationError):
            Hallmark(realization="r", description_hash="d", reference="r", outcome="exact",
                     metric="wer", measured=0.0, max_value=0.1, n_probes=1,
                     probe_provenance="trust-me")

    def test_hallmark_round_trips_and_hashes(self) -> None:
        h = _assay("tiny-asr@0.3.0", REF_WORDS, 1.0).hallmark
        restored = Hallmark.model_validate(json.loads(h.model_dump_json()))
        assert restored == h and restored.content_hash() == h.content_hash()
