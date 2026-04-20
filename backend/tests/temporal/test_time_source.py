"""Unit tests for `WallClock`, `FileClock`, and `ManualClock`."""

from __future__ import annotations

import asyncio
import time as _time

import pytest

from nodecules.core.time import FileClock, ManualClock, TimeSource, WallClock


class TestTimeSourceProtocol:
    """Sync tests — kept outside the asyncio-auto zone below."""

    def test_wall_clock_satisfies_protocol(self) -> None:
        assert isinstance(WallClock(), TimeSource)

    def test_file_clock_satisfies_protocol(self) -> None:
        assert isinstance(FileClock(), TimeSource)

    def test_manual_clock_satisfies_protocol(self) -> None:
        assert isinstance(ManualClock(), TimeSource)


class TestFileClock:
    async def test_initial_now(self) -> None:
        clk = FileClock(initial_ms=0)
        assert await clk.now_ms() == 0

    async def test_advance(self) -> None:
        clk = FileClock()
        clk.advance_to(5000)
        assert await clk.now_ms() == 5000

    async def test_cannot_rewind(self) -> None:
        clk = FileClock(initial_ms=1000)
        with pytest.raises(ValueError):
            clk.advance_to(500)

    async def test_wait_until_is_noop(self) -> None:
        clk = FileClock()
        start = _time.monotonic()
        await clk.wait_until(100_000)  # 100 seconds — must not actually sleep
        assert _time.monotonic() - start < 0.1

    async def test_close(self) -> None:
        clk = FileClock()
        assert not clk.is_closed()
        clk.close()
        assert clk.is_closed()

    async def test_negative_initial_rejected(self) -> None:
        with pytest.raises(ValueError):
            FileClock(initial_ms=-1)


class TestWallClock:
    async def test_now_starts_near_zero(self) -> None:
        clk = WallClock()
        now = await clk.now_ms()
        assert 0 <= now < 50

    async def test_now_advances(self) -> None:
        clk = WallClock()
        t0 = await clk.now_ms()
        await asyncio.sleep(0.05)
        t1 = await clk.now_ms()
        assert t1 > t0

    async def test_wait_until_past_returns_immediately(self) -> None:
        clk = WallClock()
        start = _time.monotonic()
        await clk.wait_until(0)
        assert _time.monotonic() - start < 0.05

    async def test_wait_until_sleeps_for_future_time(self) -> None:
        clk = WallClock()
        start = _time.monotonic()
        await clk.wait_until(80)
        elapsed = _time.monotonic() - start
        # Allow some scheduler slack but must have actually waited.
        assert elapsed >= 0.07

    async def test_close_prevents_future_wait(self) -> None:
        clk = WallClock()
        clk.close()
        start = _time.monotonic()
        await clk.wait_until(10_000)  # would sleep 10s if not closed
        assert _time.monotonic() - start < 0.05


class TestManualClock:
    async def test_advance_wakes_waiter(self) -> None:
        clk = ManualClock()

        async def waiter() -> int:
            await clk.wait_until(500)
            return await clk.now_ms()

        task = asyncio.create_task(waiter())
        await asyncio.sleep(0.02)
        assert not task.done()
        clk.advance_to(500)
        got = await asyncio.wait_for(task, timeout=1.0)
        assert got == 500

    async def test_close_wakes_waiter(self) -> None:
        clk = ManualClock()

        async def waiter() -> None:
            await clk.wait_until(10_000)

        task = asyncio.create_task(waiter())
        await asyncio.sleep(0.02)
        clk.close()
        await asyncio.wait_for(task, timeout=1.0)

    async def test_cannot_rewind(self) -> None:
        clk = ManualClock(initial_ms=1000)
        with pytest.raises(ValueError):
            clk.advance_to(500)
