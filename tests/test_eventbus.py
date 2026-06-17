"""Unit tests for the in-process pub/sub EventBus.

Proves replay, live delivery, multi-subscriber fan-out, and close semantics
without any network or hardware.
"""
from __future__ import annotations

import asyncio
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))

import pytest

from evcide.eventbus import EventBus


async def _drain(bus: EventBus, topic: str) -> list:
    return [m async for m in bus.subscribe(topic)]


@pytest.mark.asyncio
async def test_replay_then_close_delivers_past_messages():
    bus = EventBus()
    await bus.publish("t", {"i": 0})
    await bus.publish("t", {"i": 1})
    bus.close("t")
    got = await _drain(bus, "t")
    assert [m["i"] for m in got] == [0, 1]


@pytest.mark.asyncio
async def test_live_delivery_to_active_subscriber():
    bus = EventBus()
    received: list = []

    async def consumer():
        async for msg in bus.subscribe("t"):
            received.append(msg["i"])

    task = asyncio.create_task(consumer())
    await asyncio.sleep(0)  # let the subscriber register
    await bus.publish("t", {"i": 1})
    await bus.publish("t", {"i": 2})
    bus.close("t")
    await asyncio.wait_for(task, timeout=1.0)
    assert received == [1, 2]


@pytest.mark.asyncio
async def test_fanout_to_multiple_subscribers():
    bus = EventBus()
    a, b = [], []

    async def consume(into):
        async for msg in bus.subscribe("t"):
            into.append(msg["i"])

    ta = asyncio.create_task(consume(a))
    tb = asyncio.create_task(consume(b))
    await asyncio.sleep(0)
    await bus.publish("t", {"i": 7})
    bus.close("t")
    await asyncio.wait_for(asyncio.gather(ta, tb), timeout=1.0)
    assert a == [7] and b == [7]


@pytest.mark.asyncio
async def test_no_duplicate_when_subscribing_during_publishing():
    # A subscriber that joins after some history, then receives live messages,
    # must see each message exactly once (replay snapshot + live queue, no dup).
    bus = EventBus()
    await bus.publish("t", {"i": 0})  # history before subscribe
    seen: list = []

    async def consumer():
        async for msg in bus.subscribe("t"):
            seen.append(msg["i"])

    task = asyncio.create_task(consumer())
    await asyncio.sleep(0)
    await bus.publish("t", {"i": 1})  # live
    bus.close("t")
    await asyncio.wait_for(task, timeout=1.0)
    assert seen == [0, 1]


@pytest.mark.asyncio
async def test_reset_clears_history():
    bus = EventBus()
    await bus.publish("t", {"i": 0})
    bus.close("t")
    bus.reset("t")
    bus.close("t")
    got = await _drain(bus, "t")
    assert got == []
