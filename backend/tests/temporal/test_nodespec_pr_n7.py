"""Regression check: PR-n4 / PR-n7 NodeSpec additions are additive.

Pre-temporality node specs (no new fields populated) continue to
construct cleanly and produce the conservative defaults. This is the
static-DAG-non-regression guarantee at the spec level.
"""

from __future__ import annotations

from nodecules.core.types import NodeSpec


class TestNodeSpecAdditiveDefaults:
    def test_minimal_spec(self) -> None:
        spec = NodeSpec(
            node_type="x",
            display_name="x",
            description="",
        )
        # Temporality fields
        assert spec.temporal_kind == "static"
        assert spec.window_spec is None
        assert spec.emit_policy == "on_window_close"
        assert spec.supports_reanneal is False
        # PR-n4
        assert spec.is_deterministic is True
        assert spec.reads_strips == []
        assert spec.writes_strips == []
        # PR-n7
        assert spec.settling_windows == 0
        # PR-n8
        assert spec.reads_env == []
        assert spec.writes_env == []

    def test_settling_windows_settable(self) -> None:
        spec = NodeSpec(
            node_type="iir",
            display_name="IIR",
            description="",
            settling_windows=30,
        )
        assert spec.settling_windows == 30
