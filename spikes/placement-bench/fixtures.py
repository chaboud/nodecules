"""Fixtures: stenota's real graph, illustrative executors, and the policies.

The graph is `stenota_graph/graphs/stenota.v0.json` at stenota `0c50e5d`
(11 nodes, 15 edges), copied so the spike stands alone. The descriptions
are one per node type; only `asr/v1` and `diarize/v1` mirror the real
contracts in stenota, the rest are bench stand-ins with the right shape.

Costs are ILLUSTRATIVE — seconds to process one meeting-hour, guessed at
the right order of magnitude. `HARDWARE-TODO` H3/H6 replace them with
measured numbers; the bench's findings are about *shapes* (which lock
moves which node where, when crossings dominate), not about the digits.
"""

from __future__ import annotations

import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[2] / "backend"))

from nodecules.core.descriptions import (  # noqa: E402
    Description, ProducedStrip, SatisfiesClaim, StripRequirement, Tolerance, run_assay,
)
from nodecules.core.placement import Executor, Job, PlacementGraph, Policy  # noqa: E402
from nodecules.core.strip_access import LatestPattern, StripAccess  # noqa: E402
from nodecules.core.types import NodeSpec  # noqa: E402

GRAPH_JSON = json.loads((Path(__file__).parent / "stenota_v0.json").read_text())

# node_type -> (description name, reads, writes, metric)
SHAPES = {
    "stenota.decode_pyav":        ("decode/v1",      ("media://meeting",),                 ("audio.wav",),                 "max_abs"),
    "stenota.asr_faster_whisper": ("asr/v1",         ("audio.wav",),                       ("strips/asr/segments",),       "wer"),
    "stenota.diar_pyannote":      ("diarize/v1",     ("audio.wav",),                       ("strips/diar/segments",),      "der"),
    "stenota.turns_l2":           ("turns/v1",       ("strips/asr/segments", "strips/diar/segments"), ("strips/turns/diarized",), "max_abs"),
    "stenota.llm_tool_loop":      ("llm-lens/v1",    ("strips/turns/diarized",),           ("claims/lens",),               "max_abs"),
    "stenota.speaker_relabel":    ("relabel/v1",     ("strips/diar/segments", "claims/lens"), ("strips/diar/global",),     "der"),
    "stenota.participation":      ("participation/v1", ("strips/asr/segments", "strips/diar/global"), ("strips/participation",), "max_abs"),
    "stenota.summarizer_l2":      ("summarize-l2/v1", ("strips/turns/diarized",),          ("claims/L2",),                 "max_abs"),
    "stenota.render_markdown":    ("render/v1",      ("claims/L2",),                       ("renders/meeting.md",),        "max_abs"),
}

DESCS: dict = {}
SPECS: dict = {}
ASSAYS: dict = {}


def _describe(node_type: str) -> Description:
    name, reads, writes, metric = SHAPES[node_type]
    consumes = ()
    if name in ("asr/v1", "diarize/v1"):
        consumes = (StripRequirement(strip_name="audio.wav", pattern=LatestPattern()),)
    return Description(
        name=name, consumes=consumes,
        produces=tuple(ProducedStrip(strip_name=w, schema_id=f"schema:{w}") for w in writes),
        tolerance=Tolerance(metric=metric, max_value=0.15), reference=node_type,
    )


def _spec(realization: str, node_type: str, deterministic: bool) -> NodeSpec:
    name, reads, writes, _ = SHAPES[node_type]
    patterns = []
    if name in ("asr/v1", "diarize/v1"):
        patterns = [StripAccess(strip_name="audio.wav", pattern=LatestPattern())]
    return NodeSpec(node_type=realization, display_name=realization, description="bench",
                    reads_strips=list(reads), reads_strip_patterns=patterns,
                    writes_strips=list(writes), is_deterministic=deterministic)


def _register(realization: str, node_type: str, deterministic: bool = False) -> None:
    d = DESCS.setdefault(node_type, _describe(node_type))
    SPECS[realization] = _spec(realization, node_type, deterministic)
    ASSAYS[realization] = run_assay(d, realization, [], [], n_probes=1,
                                    probe_provenance="fresh-drawn",
                                    declared_deterministic=deterministic)


for nt in SHAPES:
    _register(nt, nt, deterministic=nt in ("stenota.decode_pyav", "stenota.turns_l2",
                                           "stenota.participation", "stenota.render_markdown"))
# The cloud serves ASR through a different realization — a substitute.
_register("whisper-api@2", "stenota.asr_faster_whisper")


def graph() -> PlacementGraph:
    jobs = []
    for node_id, node in GRAPH_JSON["nodes"].items():
        nt = node["node_type"]
        _, reads, writes, _ = SHAPES[nt]
        # The media file lives on the device: decode is pinned there.
        pinned = "mba" if nt == "stenota.decode_pyav" else None
        jobs.append(Job(node_id=node_id, description=DESCS[nt], reads=reads, writes=writes, pinned_to=pinned))
    edges = tuple((e["source"], e["target"]) for e in GRAPH_JSON["edges"])
    return PlacementGraph(graph_id=GRAPH_JSON["graph_id"], jobs=tuple(jobs), edges=edges)


def _claim(node_type: str, realization: str, grade: str, cost_class: str, locality: str) -> SatisfiesClaim:
    return SatisfiesClaim(realization=realization, description_hash=DESCS[node_type].content_hash(),
                          claimant="bench", grade=grade, cost_class=cost_class, locality=locality)


# Illustrative seconds per meeting-hour. Replace with H3/H6 measurements.
# Deliberately heterogeneous: the cloud is the better ASR and LLM, the LAN
# GPU box is the better diarizer — the realistic shape, and the one under
# which per-node binding scatters work across executors.
MBA_COST = {"stenota.decode_pyav": 60, "stenota.asr_faster_whisper": 5400, "stenota.diar_pyannote": 1200,
            "stenota.turns_l2": 2, "stenota.llm_tool_loop": 300, "stenota.speaker_relabel": 5,
            "stenota.participation": 2, "stenota.summarizer_l2": 600, "stenota.render_markdown": 1}
SPARK_COST = {"stenota.decode_pyav": 20, "stenota.asr_faster_whisper": 120, "stenota.diar_pyannote": 90,
              "stenota.turns_l2": 2, "stenota.llm_tool_loop": 40, "stenota.speaker_relabel": 5,
              "stenota.participation": 2, "stenota.summarizer_l2": 60, "stenota.render_markdown": 1}
CLOUD_COST = {"stenota.decode_pyav": 30, "whisper-api@2": 90, "stenota.diar_pyannote": 240,
              "stenota.turns_l2": 2, "stenota.llm_tool_loop": 20, "stenota.speaker_relabel": 5,
              "stenota.participation": 2, "stenota.summarizer_l2": 30, "stenota.render_markdown": 1}


def executors(*, mba_has_llm: bool = True, spark_warm: bool = True) -> tuple:
    mba_types = [nt for nt in SHAPES if mba_has_llm or nt != "stenota.llm_tool_loop"]
    mba = Executor(
        executor_id="mba", locality="on-device",
        claims=tuple(_claim(nt, nt, "reference", "local-cpu", "on-device") for nt in mba_types),
        cost={nt: float(MBA_COST[nt]) for nt in mba_types},
        cold_start={"stenota.asr_faster_whisper": 30.0, "stenota.diar_pyannote": 20.0, "stenota.llm_tool_loop": 15.0},
    )
    spark = Executor(
        executor_id="spark", locality="lan",
        claims=tuple(_claim(nt, nt, "reference", "local-gpu", "lan") for nt in SHAPES),
        cost={nt: float(SPARK_COST[nt]) for nt in SHAPES},
        warm=("stenota.asr_faster_whisper", "stenota.diar_pyannote") if spark_warm else (),
        cold_start={"stenota.asr_faster_whisper": 15.0, "stenota.diar_pyannote": 10.0},
    )
    cloud_claims = [_claim(nt, nt, "reference", "cloud", "cloud") for nt in SHAPES if nt != "stenota.asr_faster_whisper"]
    cloud_claims.append(_claim("stenota.asr_faster_whisper", "whisper-api@2", "hosted", "cloud", "cloud"))
    cloud = Executor(
        executor_id="cloud", locality="cloud", claims=tuple(cloud_claims),
        cost={k: float(v) for k, v in CLOUD_COST.items()},
    )
    return (mba, spark, cloud)


BOUNDARY = {"lan|on-device": 15.0, "cloud|on-device": 60.0, "cloud|lan": 45.0,
            "lan|lan": 5.0, "cloud|cloud": 5.0, "on-device|on-device": 5.0}


def policy(lock: str = "open", **kw) -> Policy:
    return Policy(lock_level=lock, boundary_cost=BOUNDARY, **kw)
