"""Tests for capture & replay (evcide.capture)."""
from __future__ import annotations

import asyncio
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))

from evcide.capture import CaptureSink, dump_events, load_events
from evcide.models import (
    Expectation, ExpectationKind, ReceiverDef, ReceiverKind, RuntimeEvent, VerificationContract,
)
from evcide.mutation import assess_contract
from evcide.verify import evaluate_events, overall_status


def _ev(text, t_ms, parsed=None, typ="line") -> RuntimeEvent:
    return RuntimeEvent(source=ReceiverKind.SERIAL, timestamp_ms=t_ms, board_id="b",
                        stream_id="s", type=typ, raw=text, parsed=parsed)


def test_dump_load_round_trip_is_faithful(tmp_path):
    events = [
        _ev("BOOT_OK", 100),
        _ev("imu", 350, parsed={"accel_x": 1.5, "ok": True}),
        _ev("HEARTBEAT 3", 1300, typ="line"),
    ]
    path = tmp_path / "run.jsonl"
    assert dump_events(path, events) == 3
    back = load_events(path)
    assert len(back) == 3
    # timestamps + type + raw + parsed survive exactly (timing checks depend on it)
    assert [e.timestamp_ms for e in back] == [100, 350, 1300]
    assert back[1].parsed == {"accel_x": 1.5, "ok": True}
    assert back[0].raw == "BOOT_OK"
    assert back[2].type == "line"


def test_replayed_capture_drives_engine_and_break_on_purpose(tmp_path):
    contract = VerificationContract(
        id="cap", target="t", timeout_ms=10000,
        receivers=[ReceiverDef(type=ReceiverKind.SERIAL)],
        expectations=[
            Expectation(kind=ExpectationKind.CONTAINS, pattern="BOOT_OK"),
            Expectation(kind=ExpectationKind.NO_TIMEOUT, duration_ms=500),
        ],
    )
    events = [_ev("BOOT_OK", 0)] + [_ev("hb", 200 * i) for i in range(1, 6)]
    path = tmp_path / "baseline.jsonl"
    dump_events(path, events)

    replayed = load_events(path)
    assert overall_status(evaluate_events(contract, replayed)) == "pass"
    # a captured passing run IS a break-on-purpose baseline
    assert assess_contract(contract, replayed).meaningful is True


def test_faithful_timing_within_ms_survives_replay(tmp_path):
    # within_ms depends on the ORIGINAL timestamp; a wall-clock re-stamp would break it.
    contract = VerificationContract(
        id="t", target="t", timeout_ms=10000,
        receivers=[ReceiverDef(type=ReceiverKind.SERIAL)],
        expectations=[Expectation(kind=ExpectationKind.CONTAINS, pattern="BOOT_OK", within_ms=500)],
    )
    late = [_ev("noise", 0), _ev("BOOT_OK", 4000)]   # boot at 4000ms, past the 500ms deadline
    path = tmp_path / "late.jsonl"
    dump_events(path, late)
    checks = evaluate_events(contract, load_events(path))
    assert checks[0].status == "fail"               # deadline enforced on replayed timestamps


def test_capture_sink_records_events_only(tmp_path):
    path = tmp_path / "live.jsonl"
    sink = CaptureSink(path)

    async def drive():
        await sink({"type": "event", "data": _ev("BOOT_OK", 10).model_dump(mode="json")})
        await sink({"type": "event", "data": _ev("hb", 210).model_dump(mode="json")})
        await sink({"type": "result", "data": {"status": "pass"}})  # must be skipped
    asyncio.run(drive())
    sink.close()

    events = load_events(path)
    assert sink.count == 2
    assert len(events) == 2
    assert events[0].raw == "BOOT_OK" and events[1].timestamp_ms == 210


def test_run_verification_replays_capture_through_live_path(tmp_path):
    # A .jsonl capture replays faithfully through run_verification's LIVE path
    # (streaming + sink + timeout), not just evaluate_events. The contract mixes a
    # within_ms boot check and a whole-window liveness check, so this also pins that
    # NO_TIMEOUT does not short-circuit the loop after the first event.
    from evcide.models import OutputConfig, OutputSession, now_ms
    from evcide.verify import run_verification

    events = [_ev("BOOT_OK", 100)] + [_ev("hb", 200 * i) for i in range(1, 6)]  # 200..1000
    path = tmp_path / "cap.jsonl"
    dump_events(path, events)

    contract = VerificationContract(
        id="replay", target="t", timeout_ms=2000,
        receivers=[ReceiverDef(type=ReceiverKind.FILE)],
        expectations=[
            Expectation(kind=ExpectationKind.CONTAINS, pattern="BOOT_OK", within_ms=500),
            Expectation(kind=ExpectationKind.NO_TIMEOUT, duration_ms=500),
        ],
    )
    session = OutputSession(
        stream_id="rep", receiver=ReceiverKind.FILE, board_id="b", started_at_ms=now_ms(),
        config=OutputConfig(receiver=ReceiverKind.FILE, file_path=str(path)),
    )
    result = asyncio.run(run_verification(session, contract))
    assert result.status == "pass"
    assert result.evidence[0].event_count == 6   # whole stream consumed (no premature short-circuit)


def test_replay_detects_hang_through_live_path(tmp_path):
    # A capture with a mid-stream silence gap must FAIL liveness on replay.
    from evcide.models import OutputConfig, OutputSession, now_ms
    from evcide.verify import run_verification

    events = [_ev("BOOT_OK", 0), _ev("hb", 100), _ev("hb", 5000)]  # 4900ms silence
    path = tmp_path / "hang.jsonl"
    dump_events(path, events)
    contract = VerificationContract(
        id="hang", target="t", timeout_ms=2000,
        receivers=[ReceiverDef(type=ReceiverKind.FILE)],
        expectations=[Expectation(kind=ExpectationKind.NO_TIMEOUT, duration_ms=500)],
    )
    session = OutputSession(
        stream_id="h", receiver=ReceiverKind.FILE, board_id="b", started_at_ms=now_ms(),
        config=OutputConfig(receiver=ReceiverKind.FILE, file_path=str(path)),
    )
    result = asyncio.run(run_verification(session, contract))
    assert result.status == "fail"
