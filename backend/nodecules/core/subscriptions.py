"""Strip subscriptions — push API counterpart to PR-n4's pull API.

A subscriber registers interest in a named strip; the writer / scheduler
notifies on matching events. Visibility scoping (phases, time horizon,
predicate) lets subscribers narrow their interest without forcing the
publisher to know about consumers.

Default visibility: canonical-phase events only. Other phases are
excluded by default — a consumer that wants warmup events must opt in.

Subscriptions are unbounded `asyncio.Queue` today. Slow consumers risk
memory pressure but won't block publishers. Bounded backpressure policies
(drop-oldest, drop-newest, metrics) can land additively once we have a
real workload to tune against.

PR-n6 ships the primitives. The wiring from cooker writes to `publish()`
calls lands in PR-n8 when the env's `writes_env` machinery exists —
until then, nodes call `ctx.publish()` directly when they emit.
"""

from __future__ import annotations

import asyncio
from dataclasses import dataclass, field
from typing import Any, AsyncIterator, Callable, Optional

from .time import TimeRange
from .types import DerivationPhase


# Default visibility set — canonical events only. Frozen so it's safe to
# share across many Visibility instances without copy-on-write concerns.
_DEFAULT_PHASES: frozenset[DerivationPhase] = frozenset({DerivationPhase.CANONICAL})


@dataclass(frozen=True)
class Visibility:
    """Scoping for a strip subscription.

    `phases`: which phases the subscriber wants. Default: canonical only.

    `time_horizon`: optional callable mapping `now_ms` to an acceptable
    `TimeRange`. Events whose `time_range` doesn't intersect the horizon
    are dropped. `None` means "all times accepted." Callable form lets
    the horizon shift as the subscriber's `now` advances.

    `predicate`: optional event-level filter, called with the raw event.
    `None` means "all events accepted."
    """

    phases: frozenset[DerivationPhase] = field(default_factory=lambda: _DEFAULT_PHASES)
    time_horizon: Optional[Callable[[int], Optional[TimeRange]]] = None
    predicate: Optional[Callable[[Any], bool]] = None


class Subscription:
    """One subscriber's view of a strip.

    Async-iterable: `async for evt in sub: ...`. The iterator completes
    when the subscription is closed (via `unsubscribe()` or
    `close_all()`).

    Each subscription has its own queue; the manager broadcasts to all
    matching subscriptions — a slow consumer affects only its own
    backlog.
    """

    def __init__(self, strip_name: str, visibility: Visibility) -> None:
        self.strip_name = strip_name
        self.visibility = visibility
        self._queue: asyncio.Queue[Any] = asyncio.Queue()
        self._closed = False
        # Distinguishable from any user event — a private sentinel object.
        # Comparison uses identity (`is`), not equality, so user events
        # that happen to compare equal to a string can't fake closure.
        self._sentinel: Any = object()

    def __aiter__(self) -> AsyncIterator[Any]:
        return self._iter()

    async def _iter(self) -> AsyncIterator[Any]:
        while True:
            item = await self._queue.get()
            if item is self._sentinel:
                return
            yield item

    def _deliver(self, event: Any) -> None:
        """Internal: enqueue an event. No-op if closed."""
        if self._closed:
            return
        self._queue.put_nowait(event)

    def close(self) -> None:
        """Stop accepting new events; signal the iterator to end after
        the currently-queued events drain."""
        if self._closed:
            return
        self._closed = True
        self._queue.put_nowait(self._sentinel)

    @property
    def is_closed(self) -> bool:
        return self._closed

    @property
    def qsize(self) -> int:
        """Number of events currently buffered (including the sentinel
        after close)."""
        return self._queue.qsize()


class SubscriptionManager:
    """Routes published events to matching subscriptions.

    One manager per execution context. Not thread-safe across event
    loops — assume a single asyncio loop per cooker. Multi-loop
    deployments use multiple managers, one per loop.
    """

    def __init__(
        self, now_provider: Optional[Callable[[], int]] = None
    ) -> None:
        # strip_name -> list of subscriptions
        self._by_strip: dict[str, list[Subscription]] = {}
        # Clock for time-horizon evaluation. `None` callers degrade the
        # time filter (no horizon-based filtering happens) rather than
        # silently producing wrong results.
        self._now = now_provider or (lambda: 0)

    def subscribe(
        self,
        strip_name: str,
        visibility: Optional[Visibility] = None,
    ) -> Subscription:
        sub = Subscription(strip_name, visibility or Visibility())
        self._by_strip.setdefault(strip_name, []).append(sub)
        return sub

    def unsubscribe(self, sub: Subscription) -> None:
        """Close the subscription and drop it from routing.

        Pending events queued before unsubscribe still drain to the
        iterator — callers reading `async for evt in sub:` will see
        every event published while the subscription was alive.
        """
        sub.close()
        lst = self._by_strip.get(sub.strip_name)
        if lst is not None:
            try:
                lst.remove(sub)
            except ValueError:
                pass
            if not lst:
                self._by_strip.pop(sub.strip_name, None)

    def publish(
        self,
        strip_name: str,
        event: Any,
        *,
        phase: DerivationPhase = DerivationPhase.CANONICAL,
        time_range: Optional[TimeRange] = None,
    ) -> int:
        """Notify every subscription whose visibility accepts this event.

        Returns the number of subscriptions notified. Idempotent in the
        sense that publishing twice delivers twice — subscribers are
        expected to dedupe at their end if that matters.
        """
        subs = self._by_strip.get(strip_name, [])
        if not subs:
            return 0
        now_ms = self._now()
        delivered = 0
        for sub in list(subs):
            if sub.is_closed:
                continue
            if phase not in sub.visibility.phases:
                continue
            if (
                sub.visibility.time_horizon is not None
                and time_range is not None
            ):
                horizon = sub.visibility.time_horizon(now_ms)
                if horizon is not None and not horizon.intersects(time_range):
                    continue
            if sub.visibility.predicate is not None and not sub.visibility.predicate(event):
                continue
            sub._deliver(event)
            delivered += 1
        return delivered

    def close_all(self) -> None:
        """Close every subscription and clear registrations."""
        for subs in self._by_strip.values():
            for sub in subs:
                sub.close()
        self._by_strip.clear()

    def has_subscribers(self, strip_name: str) -> bool:
        """True iff at least one open subscription exists for `strip_name`.

        Lets a writer skip the publish work for un-listened strips. Cheap.
        """
        lst = self._by_strip.get(strip_name)
        if not lst:
            return False
        return any(not sub.is_closed for sub in lst)


__all__ = [
    "Subscription",
    "SubscriptionManager",
    "Visibility",
]
