"""Tests for timelines — clock domain, resolution, and skew as data."""

from __future__ import annotations

from fractions import Fraction

import pytest

from nodecules.core.time import TimeRange
from nodecules.core.timeline import (
    MILLISECONDS,
    NANOS,
    NANOSECONDS,
    Anchor,
    Instant,
    Span,
    Timebase,
    Timeline,
    TimelineMap,
    convert,
    convert_span,
    frame_clock,
    inverse,
    sample_clock,
)

MEETING = Timeline(id="meeting", timebase=MILLISECONDS, origin="meeting-start", clock="derived")
MIC = Timeline(id="mic", timebase=sample_clock(48_000), origin="media-start", clock="sample-counter", source="usb-mic-0")
CAM = Timeline(id="cam", timebase=frame_clock(30_000, 1001), origin="media-start", clock="media", source="cam-1")
LAPTOP = Timeline(id="laptop", timebase=NANOSECONDS, origin="device-boot", clock="monotonic", source="macbook-air")
PHONE = Timeline(id="phone", timebase=NANOSECONDS, origin="device-boot", clock="monotonic", source="iphone")
VEGAS = Timeline(id="project", timebase=NANOS, origin="media-start", clock="derived")
T = {t.id: t for t in (MEETING, MIC, CAM, LAPTOP, PHONE, VEGAS)}


def test_timebase_is_kept_in_lowest_terms_and_is_exact():
    assert Timebase(num=2, den=2000) == MILLISECONDS
    assert sample_clock(48_000).seconds_per_tick() == Fraction(1, 48_000)
    assert frame_clock(30_000, 1001).seconds_per_tick() == Fraction(1001, 30_000)
    assert NANOS.ticks_per_second() == 10_000_000
    with pytest.raises(ValueError):
        Timebase(num=0, den=1)


def test_timeline_carries_domain_and_provenance_as_data():
    d = MIC.as_node_data()
    assert d["clock"] == "sample-counter" and d["source"] == "usb-mic-0" and d["origin"] == "media-start"
    assert d["timebase"] == {"num": 1, "den": 48_000}


def test_span_is_time_range_generalised_and_bridges_both_ways():
    tr = TimeRange(start_ms=1_000, end_ms=2_500)
    s = Span.from_time_range(tr, "meeting")
    assert s.duration() == 1_500 and s.contains(1_000) and not s.contains(2_500)
    assert s.to_time_range(T) == tr
    with pytest.raises(ValueError):
        Span(start=0, end=10, timeline="mic").to_time_range(T)  # not a millisecond timeline
    with pytest.raises(ValueError):
        Span(start=5, end=5, timeline="meeting")


def test_spans_on_different_timelines_refuse_to_compare():
    a = Span(start=0, end=10, timeline="meeting")
    b = Span(start=0, end=10, timeline="mic")
    with pytest.raises(ValueError, match="convert first"):
        a.intersects(b)


def test_sample_positions_convert_exactly_when_they_can_and_say_when_they_cannot():
    m = TimelineMap(src="mic", dst="meeting", anchors=(Anchor(src=0, dst=0),), provenance="declared")
    whole_second = convert(Instant(ticks=48_000, timeline="mic"), m, T)
    assert whole_second.instant == Instant(ticks=1_000, timeline="meeting") and whole_second.exact
    hundred_samples = convert(Instant(ticks=100, timeline="mic"), m, T)  # 2.0833 ms
    assert hundred_samples.instant.ticks == 2 and not hundred_samples.exact
    assert convert(Instant(ticks=100, timeline="mic"), m, T, rounding="ceil").instant.ticks == 3
    assert hundred_samples.provenance == "declared"


def test_milliseconds_to_vegas_nanos_is_exact_and_back_is_not_always():
    up = TimelineMap(src="meeting", dst="project", anchors=(Anchor(src=0, dst=0),), provenance="declared")
    c = convert(Instant(ticks=1, timeline="meeting"), up, T)
    assert c.instant.ticks == 10_000 and c.exact
    down = inverse(up)
    assert convert(Instant(ticks=10_000, timeline="project"), down, T).exact
    assert not convert(Instant(ticks=10_001, timeline="project"), down, T).exact


def test_video_frames_are_exact_in_their_own_timeline_and_rounded_in_ms():
    m = TimelineMap(src="cam", dst="meeting", anchors=(Anchor(src=0, dst=0),), provenance="declared")
    frame = convert(Instant(ticks=1, timeline="cam"), m, T)
    assert frame.instant.ticks == 33 and not frame.exact  # 33.366… ms
    thirty = convert(Instant(ticks=30, timeline="cam"), m, T)
    assert thirty.instant.ticks == 1_001 and thirty.exact


def test_skew_between_two_devices_is_carried_by_measured_anchors():
    """Two monotonic clocks that started at different moments and drift
    by 200 ppm: anchors express both, and the map says it was measured."""
    ten_min = 600 * 10**9
    m = TimelineMap(
        src="laptop",
        dst="phone",
        anchors=(Anchor(src=0, dst=5 * 10**9), Anchor(src=ten_min, dst=5 * 10**9 + ten_min + 120 * 10**6)),
        provenance="measured",
        error_ticks=2 * 10**6,  # ± 2 ms
    )
    assert m.measured_rate() == Fraction(ten_min + 120 * 10**6, ten_min)
    mid = convert(Instant(ticks=300 * 10**9, timeline="laptop"), m, T)
    assert mid.instant.ticks == 5 * 10**9 + 300 * 10**9 + 60 * 10**6  # half the drift at half the span
    assert mid.provenance == "measured" and mid.error_ticks == 2 * 10**6
    # beyond the last anchor the map extrapolates at nominal rate, not the measured drift
    later = convert(Instant(ticks=ten_min + 10**9, timeline="laptop"), m, T)
    assert later.instant.ticks == 5 * 10**9 + ten_min + 120 * 10**6 + 10**9


def test_anchors_must_be_ordered_and_a_self_map_is_refused():
    with pytest.raises(ValueError):
        TimelineMap(src="a", dst="b", anchors=(Anchor(src=10, dst=0), Anchor(src=0, dst=0)))
    with pytest.raises(ValueError):
        TimelineMap(src="a", dst="a", anchors=(Anchor(src=0, dst=0),))
    with pytest.raises(ValueError):
        convert(Instant(ticks=0, timeline="meeting"), TimelineMap(src="mic", dst="meeting", anchors=(Anchor(src=0, dst=0),)), T)


def test_converted_span_covers_the_original():
    m = TimelineMap(src="mic", dst="meeting", anchors=(Anchor(src=0, dst=0),), provenance="declared")
    s, exact = convert_span(Span(start=100, end=250, timeline="mic"), m, T)  # 2.08 ms .. 5.21 ms
    assert s == Span(start=2, end=6, timeline="meeting") and not exact
    s2, exact2 = convert_span(Span(start=0, end=48_000, timeline="mic"), m, T)
    assert s2 == Span(start=0, end=1_000, timeline="meeting") and exact2
