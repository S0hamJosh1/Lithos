"""BLE tests.

The verify-side checks (ble_advertising, gatt_service_present) are exercised with
synthetic BLE events — no radio needed. The bleak receiver itself is
hardware-untested; here we only assert it fails cleanly when bleak is absent.
"""
import asyncio
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))

import pytest

from evcide import receivers
from evcide.models import (
    Expectation, ExpectationKind, OutputConfig, OutputSession, ReceiverDef,
    ReceiverKind, RuntimeEvent, VerificationContract, now_ms,
)
from evcide.verify import run_verification


def _session(receiver=ReceiverKind.BLE) -> OutputSession:
    return OutputSession(
        stream_id="strm-ble", receiver=receiver, board_id="ble-board",
        started_at_ms=now_ms(), config=OutputConfig(receiver=receiver, ble_filter={}),
    )


def _make_stream(events):
    async def stream(session, stop_event):
        for ev in events:
            if stop_event.is_set():
                return
            yield ev
            await asyncio.sleep(0)
    return stream


def _ble_event(type_: str, parsed=None, raw=None) -> RuntimeEvent:
    return RuntimeEvent(source=ReceiverKind.BLE, timestamp_ms=now_ms(),
                        board_id="ble-board", stream_id="strm-ble",
                        type=type_, raw=raw, parsed=parsed)


@pytest.mark.asyncio
async def test_ble_advertising_pass_with_name_filter(monkeypatch):
    events = [_ble_event("advertisement", {"name": "XIAO-IMU", "rssi": -50})]
    monkeypatch.setattr(receivers, "get_receiver_stream", lambda k: _make_stream(events))
    contract = VerificationContract(
        id="b1", target="seeed_xiao_nrf52840_sense", timeout_ms=1000,
        receivers=[ReceiverDef(type=ReceiverKind.BLE)],
        expectations=[Expectation(kind=ExpectationKind.BLE_ADVERTISING, pattern="XIAO")],
    )
    result = await run_verification(_session(), contract)
    assert result.status == "pass"


@pytest.mark.asyncio
async def test_ble_advertising_name_mismatch_not_pass(monkeypatch):
    events = [_ble_event("advertisement", {"name": "OTHER-DEV"})]
    monkeypatch.setattr(receivers, "get_receiver_stream", lambda k: _make_stream(events))
    contract = VerificationContract(
        id="b2", target="seeed_xiao_nrf52840_sense", timeout_ms=500,
        receivers=[ReceiverDef(type=ReceiverKind.BLE)],
        expectations=[Expectation(kind=ExpectationKind.BLE_ADVERTISING, pattern="XIAO")],
    )
    result = await run_verification(_session(), contract)
    assert result.status != "pass"


@pytest.mark.asyncio
async def test_gatt_service_present_pass(monkeypatch):
    uuid = "0000180f-0000-1000-8000-00805f9b34fb"
    events = [_ble_event("connect", {"service_uuids": [uuid, "0000180a-..."]})]
    monkeypatch.setattr(receivers, "get_receiver_stream", lambda k: _make_stream(events))
    contract = VerificationContract(
        id="b3", target="seeed_xiao_nrf52840_sense", timeout_ms=1000,
        receivers=[ReceiverDef(type=ReceiverKind.BLE)],
        expectations=[Expectation(kind=ExpectationKind.GATT_SERVICE_PRESENT, service_uuid=uuid)],
    )
    result = await run_verification(_session(), contract)
    assert result.status == "pass"


@pytest.mark.asyncio
async def test_gatt_service_absent_is_inconclusive(monkeypatch):
    events = [_ble_event("connect", {"service_uuids": ["0000180a-0000-1000-8000-00805f9b34fb"]})]
    monkeypatch.setattr(receivers, "get_receiver_stream", lambda k: _make_stream(events))
    contract = VerificationContract(
        id="b4", target="seeed_xiao_nrf52840_sense", timeout_ms=500,
        receivers=[ReceiverDef(type=ReceiverKind.BLE)],
        expectations=[Expectation(kind=ExpectationKind.GATT_SERVICE_PRESENT,
                                  service_uuid="0000180f-0000-1000-8000-00805f9b34fb")],
    )
    result = await run_verification(_session(), contract)
    assert result.status == "inconclusive"


@pytest.mark.asyncio
async def test_ble_receiver_raises_cleanly_without_bleak():
    try:
        import bleak  # noqa: F401
        pytest.skip("bleak is installed; not-installed path not applicable")
    except ImportError:
        pass
    stop = asyncio.Event()
    with pytest.raises(RuntimeError, match="bleak"):
        async for _ in receivers.ble_receiver_stream(_session(), stop):
            break
