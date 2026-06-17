"""Smoke tests for the verification engine.

Drives the engine with a synthetic receiver so no real hardware is needed.
Proves the contract -> measurable PASS/FAIL pipeline works end-to-end.
"""
from __future__ import annotations

import asyncio
import math
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))

import pytest

from evcide import receivers
from evcide.models import (
    ExpectationKind, OutputConfig, OutputSession, ReceiverKind, ReceiverDef,
    RuntimeEvent, VerificationContract, Expectation, now_ms,
)
from evcide.verify import run_verification


# ---- helpers ----

def _session(receiver=ReceiverKind.SERIAL) -> OutputSession:
    return OutputSession(
        stream_id="strm-test",
        receiver=receiver,
        board_id="test-board",
        started_at_ms=now_ms(),
        config=OutputConfig(receiver=receiver, serial_port="/dev/null", serial_baud=115200),
    )


def _make_synthetic_stream(events: list[RuntimeEvent]):
    async def stream(session, stop_event):
        for ev in events:
            if stop_event.is_set():
                return
            yield ev
            await asyncio.sleep(0)
    return stream


def _ev(text: str, t_ms: int, parsed=None) -> RuntimeEvent:
    return RuntimeEvent(
        source=ReceiverKind.SERIAL, timestamp_ms=t_ms,
        board_id="test-board", stream_id="strm-test",
        type="line", raw=text, parsed=parsed,
    )


# ---- tests ----

@pytest.mark.asyncio
async def test_contains_passes_on_boot_token(monkeypatch):
    events = [_ev("BOOT_OK ready", 100), _ev("HEARTBEAT 1", 1100)]
    monkeypatch.setattr(receivers, "get_receiver_stream",
                        lambda k: _make_synthetic_stream(events))

    contract = VerificationContract(
        id="t1", target="seeed_xiao_nrf52840_sense", timeout_ms=2000,
        receivers=[ReceiverDef(type=ReceiverKind.SERIAL, port="auto")],
        expectations=[
            Expectation(kind=ExpectationKind.CONTAINS, pattern="BOOT_OK"),
        ],
    )
    result = await run_verification(_session(), contract)
    assert result.status == "pass"
    assert result.checks[0].status == "pass"


@pytest.mark.asyncio
async def test_no_runtime_data_classifies_as_no_serial(monkeypatch):
    monkeypatch.setattr(receivers, "get_receiver_stream",
                        lambda k: _make_synthetic_stream([]))
    contract = VerificationContract(
        id="t2", target="seeed_xiao_nrf52840_sense", timeout_ms=600,
        receivers=[ReceiverDef(type=ReceiverKind.SERIAL, port="auto")],
        expectations=[Expectation(kind=ExpectationKind.CONTAINS, pattern="BOOT_OK")],
    )
    result = await run_verification(_session(), contract)
    assert result.status in ("fail", "inconclusive")
    assert result.failure_classification == "no_serial_data"


@pytest.mark.asyncio
async def test_count_min_pass_after_n(monkeypatch):
    events = [_ev(f"LED_TOGGLE {i}", 100 + i * 50) for i in range(4)]
    monkeypatch.setattr(receivers, "get_receiver_stream",
                        lambda k: _make_synthetic_stream(events))
    contract = VerificationContract(
        id="t3", target="seeed_xiao_nrf52840_sense", timeout_ms=2000,
        receivers=[ReceiverDef(type=ReceiverKind.SERIAL, port="auto")],
        expectations=[Expectation(kind=ExpectationKind.COUNT_MIN, pattern="LED_TOGGLE", count=3)],
    )
    result = await run_verification(_session(), contract)
    assert result.status == "pass"
    assert result.checks[0].actual["count"] >= 3


@pytest.mark.asyncio
async def test_field_not_frozen_detects_stuck_sensor(monkeypatch):
    events = [_ev(f"ax=1.0 ay=2.0 az=9.8", 100 + i * 20, {"ax": 1.0, "ay": 2.0, "az": 9.8})
              for i in range(10)]
    monkeypatch.setattr(receivers, "get_receiver_stream",
                        lambda k: _make_synthetic_stream(events))
    contract = VerificationContract(
        id="t4", target="seeed_xiao_nrf52840_sense", timeout_ms=1000,
        receivers=[ReceiverDef(type=ReceiverKind.SERIAL, port="auto")],
        expectations=[Expectation(kind=ExpectationKind.FIELD_NOT_FROZEN, field="ax")],
    )
    result = await run_verification(_session(), contract)
    assert result.status == "fail"
    assert result.failure_classification == "imu_frozen"


@pytest.mark.asyncio
async def test_within_ms_passes_when_boot_is_prompt(monkeypatch):
    # BOOT_OK is the first event -> elapsed 0 -> well inside a 500ms deadline.
    events = [_ev("BOOT_OK", 1000), _ev("HEARTBEAT 1", 1100)]
    monkeypatch.setattr(receivers, "get_receiver_stream",
                        lambda k: _make_synthetic_stream(events))
    contract = VerificationContract(
        id="w1", target="seeed_xiao_nrf52840_sense", timeout_ms=2000,
        receivers=[ReceiverDef(type=ReceiverKind.SERIAL, port="auto")],
        expectations=[Expectation(kind=ExpectationKind.CONTAINS, pattern="BOOT_OK",
                                  within_ms=500)],
    )
    result = await run_verification(_session(), contract)
    assert result.status == "pass"


@pytest.mark.asyncio
async def test_within_ms_fails_when_boot_is_late(monkeypatch):
    # First event at t0=100; BOOT_OK only at +2000ms, past a 1000ms deadline.
    events = [_ev("starting...", 100), _ev("BOOT_OK", 2100)]
    monkeypatch.setattr(receivers, "get_receiver_stream",
                        lambda k: _make_synthetic_stream(events))
    contract = VerificationContract(
        id="w2", target="seeed_xiao_nrf52840_sense", timeout_ms=3000,
        receivers=[ReceiverDef(type=ReceiverKind.SERIAL, port="auto")],
        expectations=[Expectation(kind=ExpectationKind.CONTAINS, pattern="BOOT_OK",
                                  within_ms=1000)],
    )
    result = await run_verification(_session(), contract)
    assert result.status == "fail"
    assert "within_ms" in result.checks[0].message


@pytest.mark.asyncio
async def test_within_ms_fails_when_never_satisfied(monkeypatch):
    # Events present (so not a no-data case) but the token never appears.
    events = [_ev("noise", 100), _ev("more noise", 200)]
    monkeypatch.setattr(receivers, "get_receiver_stream",
                        lambda k: _make_synthetic_stream(events))
    contract = VerificationContract(
        id="w3", target="seeed_xiao_nrf52840_sense", timeout_ms=1000,
        receivers=[ReceiverDef(type=ReceiverKind.SERIAL, port="auto")],
        expectations=[Expectation(kind=ExpectationKind.CONTAINS, pattern="BOOT_OK",
                                  within_ms=500)],
    )
    result = await run_verification(_session(), contract)
    assert result.status == "fail"
    assert result.checks[0].status == "fail"


@pytest.mark.asyncio
async def test_after_ms_skips_warmup_events(monkeypatch):
    # First sample is out-of-range warm-up garbage; after_ms gates it out so the
    # later in-range samples make the check pass.
    events = [
        _ev("ax=99.0", 100, {"ax": 99.0}),     # elapsed 0, would FAIL field_range
        _ev("ax=1.0", 700, {"ax": 1.0}),       # elapsed 600, in range
        _ev("ax=2.0", 900, {"ax": 2.0}),       # elapsed 800, in range
    ]
    monkeypatch.setattr(receivers, "get_receiver_stream",
                        lambda k: _make_synthetic_stream(events))
    contract = VerificationContract(
        id="a1", target="seeed_xiao_nrf52840_sense", timeout_ms=2000,
        receivers=[ReceiverDef(type=ReceiverKind.SERIAL, port="auto")],
        expectations=[Expectation(kind=ExpectationKind.FIELD_RANGE, field="ax",
                                  value_min=-20.0, value_max=20.0, after_ms=500)],
    )
    result = await run_verification(_session(), contract)
    assert result.status == "pass", result.checks[0].message


@pytest.mark.asyncio
async def test_message_rate_in_band(monkeypatch):
    # 50 Hz nominal -> one sample every 20ms over 500ms = 25 samples
    events = [_ev(f"tick {i}", 100 + i * 20, {"i": i}) for i in range(25)]
    monkeypatch.setattr(receivers, "get_receiver_stream",
                        lambda k: _make_synthetic_stream(events))
    contract = VerificationContract(
        id="t5", target="seeed_xiao_nrf52840_sense", timeout_ms=1000,
        receivers=[ReceiverDef(type=ReceiverKind.SERIAL, port="auto")],
        expectations=[Expectation(
            kind=ExpectationKind.MESSAGE_RATE_HZ,
            expected_rate_hz=50.0, rate_tolerance_pct=30.0,
        )],
    )
    result = await run_verification(_session(), contract)
    # synthetic events have no real sleep so timing reflects synthetic timestamps
    rate = result.checks[0].actual.get("rate_hz", 0)
    assert 35 <= rate <= 65, f"rate out of band: {rate}"
