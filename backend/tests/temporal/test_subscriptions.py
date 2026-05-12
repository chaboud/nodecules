"""Tests for PR-n6 strip subscriptions.

The primitives `Subscription`, `SubscriptionManager`, `Visibility`, and the
`DerivationPhase` enum cover the push API. Wiring through `ChunkedContext`
is tested in `test_temporal_context_strips_and_subs.py`.
"""

from __future__ import annotations

import pytest

from nodecules.core.subscriptions import (
    Subscription,
    SubscriptionManager,
    Visibility,
)
from nodecules.core.time import TimeRange
from nodecules.core.types import DerivationPhase


async def _drain(sub: Subscription) -> list:
    """Helper: collect every event from an iterator that has been closed."""
    return [item async for item in sub]


class TestSubscribePublish:
    async def test_single_subscriber_receives_canonical(self) -> None:
        mgr = SubscriptionManager()
        sub = mgr.subscribe("strips/x")
        mgr.publish("strips/x", "hello", phase=DerivationPhase.CANONICAL)
        mgr.publish("strips/x", "world", phase=DerivationPhase.CANONICAL)
        mgr.unsubscribe(sub)
        assert await _drain(sub) == ["hello", "world"]

    async def test_multiple_subscribers_each_get_copy(self) -> None:
        mgr = SubscriptionManager()
        a = mgr.subscribe("s")
        b = mgr.subscribe("s")
        mgr.publish("s", "evt", phase=DerivationPhase.CANONICAL)
        mgr.unsubscribe(a)
        mgr.unsubscribe(b)
        assert await _drain(a) == ["evt"]
        assert await _drain(b) == ["evt"]

    async def test_no_subscribers_returns_zero(self) -> None:
        mgr = SubscriptionManager()
        n = mgr.publish("nobody", "x", phase=DerivationPhase.CANONICAL)
        assert n == 0

    async def test_publish_returns_delivery_count(self) -> None:
        mgr = SubscriptionManager()
        a = mgr.subscribe("s")
        b = mgr.subscribe("s")
        assert mgr.publish("s", "x", phase=DerivationPhase.CANONICAL) == 2
        mgr.unsubscribe(b)
        assert mgr.publish("s", "y", phase=DerivationPhase.CANONICAL) == 1
        mgr.unsubscribe(a)

    async def test_has_subscribers(self) -> None:
        mgr = SubscriptionManager()
        assert mgr.has_subscribers("s") is False
        sub = mgr.subscribe("s")
        assert mgr.has_subscribers("s") is True
        mgr.unsubscribe(sub)
        assert mgr.has_subscribers("s") is False


class TestPhaseFiltering:
    async def test_default_excludes_warmup_and_superseded(self) -> None:
        mgr = SubscriptionManager()
        sub = mgr.subscribe("s")
        mgr.publish("s", "warm", phase=DerivationPhase.WARMUP)
        mgr.publish("s", "canon", phase=DerivationPhase.CANONICAL)
        mgr.publish("s", "old", phase=DerivationPhase.SUPERSEDED)
        mgr.unsubscribe(sub)
        assert await _drain(sub) == ["canon"]

    async def test_opt_in_warmup(self) -> None:
        mgr = SubscriptionManager()
        vis = Visibility(
            phases=frozenset({DerivationPhase.WARMUP, DerivationPhase.CANONICAL})
        )
        sub = mgr.subscribe("s", vis)
        mgr.publish("s", "warm", phase=DerivationPhase.WARMUP)
        mgr.publish("s", "canon", phase=DerivationPhase.CANONICAL)
        mgr.publish("s", "old", phase=DerivationPhase.SUPERSEDED)
        mgr.unsubscribe(sub)
        assert await _drain(sub) == ["warm", "canon"]

    async def test_subscriber_can_pick_only_superseded(self) -> None:
        """Pathological but legal: an auditor subscribing to superseded
        events to track what changed."""
        mgr = SubscriptionManager()
        vis = Visibility(phases=frozenset({DerivationPhase.SUPERSEDED}))
        sub = mgr.subscribe("s", vis)
        mgr.publish("s", "canon", phase=DerivationPhase.CANONICAL)
        mgr.publish("s", "old", phase=DerivationPhase.SUPERSEDED)
        mgr.unsubscribe(sub)
        assert await _drain(sub) == ["old"]


class TestTimeHorizon:
    async def test_horizon_excludes_out_of_range(self) -> None:
        mgr = SubscriptionManager(now_provider=lambda: 1_000)
        vis = Visibility(
            time_horizon=lambda now: TimeRange(start_ms=now - 100, end_ms=now),
        )
        sub = mgr.subscribe("s", vis)
        mgr.publish(
            "s",
            "old",
            phase=DerivationPhase.CANONICAL,
            time_range=TimeRange(start_ms=0, end_ms=10),
        )
        mgr.publish(
            "s",
            "recent",
            phase=DerivationPhase.CANONICAL,
            time_range=TimeRange(start_ms=950, end_ms=1_000),
        )
        mgr.unsubscribe(sub)
        assert await _drain(sub) == ["recent"]

    async def test_no_time_range_on_event_skips_horizon_check(self) -> None:
        """Events published without a time_range can't be horizon-filtered;
        they're delivered to all phase-matching subscribers regardless."""
        mgr = SubscriptionManager(now_provider=lambda: 1_000)
        vis = Visibility(
            time_horizon=lambda now: TimeRange(start_ms=now - 1, end_ms=now),
        )
        sub = mgr.subscribe("s", vis)
        mgr.publish("s", "untimed", phase=DerivationPhase.CANONICAL)
        mgr.unsubscribe(sub)
        assert await _drain(sub) == ["untimed"]

    async def test_horizon_advances_with_now(self) -> None:
        """now_provider returning different values across calls produces
        sliding-window behavior."""
        now_ms = [500]
        mgr = SubscriptionManager(now_provider=lambda: now_ms[0])
        vis = Visibility(
            time_horizon=lambda now: TimeRange(start_ms=now - 100, end_ms=now),
        )
        sub = mgr.subscribe("s", vis)
        # At now=500, horizon is [400, 500). Event at [350, 380) is out.
        mgr.publish(
            "s",
            "a",
            phase=DerivationPhase.CANONICAL,
            time_range=TimeRange(start_ms=350, end_ms=380),
        )
        # Advance now.
        now_ms[0] = 600
        # Now horizon is [500, 600). Event at [550, 580) is in.
        mgr.publish(
            "s",
            "b",
            phase=DerivationPhase.CANONICAL,
            time_range=TimeRange(start_ms=550, end_ms=580),
        )
        mgr.unsubscribe(sub)
        assert await _drain(sub) == ["b"]


class TestPredicate:
    async def test_predicate_filter(self) -> None:
        mgr = SubscriptionManager()
        vis = Visibility(
            predicate=lambda e: isinstance(e, dict) and e.get("keep") is True
        )
        sub = mgr.subscribe("s", vis)
        mgr.publish("s", {"keep": True, "n": 1}, phase=DerivationPhase.CANONICAL)
        mgr.publish("s", {"keep": False, "n": 2}, phase=DerivationPhase.CANONICAL)
        mgr.publish("s", {"keep": True, "n": 3}, phase=DerivationPhase.CANONICAL)
        mgr.unsubscribe(sub)
        items = await _drain(sub)
        assert [i["n"] for i in items] == [1, 3]


class TestLifecycle:
    async def test_unsubscribed_no_longer_receives(self) -> None:
        mgr = SubscriptionManager()
        sub = mgr.subscribe("s")
        mgr.publish("s", "one", phase=DerivationPhase.CANONICAL)
        mgr.unsubscribe(sub)
        # After unsubscribe, publishes don't reach this subscription.
        n = mgr.publish("s", "two", phase=DerivationPhase.CANONICAL)
        assert n == 0  # nobody else listening
        assert await _drain(sub) == ["one"]

    async def test_close_all(self) -> None:
        mgr = SubscriptionManager()
        a = mgr.subscribe("s")
        b = mgr.subscribe("t")
        mgr.publish("s", "x", phase=DerivationPhase.CANONICAL)
        mgr.close_all()
        n = mgr.publish("s", "y", phase=DerivationPhase.CANONICAL)
        assert n == 0
        assert await _drain(a) == ["x"]
        assert await _drain(b) == []

    async def test_double_close_is_safe(self) -> None:
        mgr = SubscriptionManager()
        sub = mgr.subscribe("s")
        sub.close()
        sub.close()  # must not raise
        assert sub.is_closed
        assert await _drain(sub) == []

    async def test_qsize_visible(self) -> None:
        mgr = SubscriptionManager()
        sub = mgr.subscribe("s")
        mgr.publish("s", "a", phase=DerivationPhase.CANONICAL)
        mgr.publish("s", "b", phase=DerivationPhase.CANONICAL)
        assert sub.qsize == 2
        mgr.unsubscribe(sub)
        # close adds a sentinel — size is 3 until iteration drains it.
        assert sub.qsize == 3
