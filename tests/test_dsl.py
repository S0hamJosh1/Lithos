"""Tests for the contract DSL (evcide.dsl)."""
from __future__ import annotations

import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))

import pytest

from evcide.dsl import DSLError, parse_contract, parse_expectation
from evcide.models import ExpectationKind, ReceiverKind


def test_contains_with_timing():
    e = parse_expectation("contains BOOT_OK within_ms:3000 after_ms:100")
    assert e.kind == ExpectationKind.CONTAINS
    assert e.pattern == "BOOT_OK"
    assert e.within_ms == 3000 and e.after_ms == 100


def test_sequence_and_counts():
    s = parse_expectation("sequence INIT,CALIB,READY within_ms:5000")
    assert s.kind == ExpectationKind.SEQUENCE and s.sequence == ["INIT", "CALIB", "READY"]
    c = parse_expectation("count_min HEARTBEAT 5")
    assert c.kind == ExpectationKind.COUNT_MIN and c.pattern == "HEARTBEAT" and c.count == 5
    cm = parse_expectation("count_max ERROR 0")
    assert cm.kind == ExpectationKind.COUNT_MAX and cm.count == 0


def test_rate_forms():
    band = parse_expectation("rate expected:50 tol:10")
    assert band.expected_rate_hz == 50 and band.rate_tolerance_pct == 10
    lo = parse_expectation("rate min:10")
    assert lo.min_rate_hz == 10 and lo.max_rate_hz is None
    both = parse_expectation("rate min:10 max:60")
    assert both.min_rate_hz == 10 and both.max_rate_hz == 60


def test_field_checks_and_range():
    assert parse_expectation("field accel_x not_frozen").kind == ExpectationKind.FIELD_NOT_FROZEN
    assert parse_expectation("field temp no_nan").kind == ExpectationKind.NO_NAN
    assert parse_expectation("field gx present").kind == ExpectationKind.FIELD_PRESENT
    r = parse_expectation("field accel_x range:-2..2")
    assert r.kind == ExpectationKind.FIELD_RANGE and r.field == "accel_x"
    assert r.value_min == -2.0 and r.value_max == 2.0


def test_liveness_ble_socket():
    assert parse_expectation("no_timeout 1000").duration_ms == 1000
    assert parse_expectation("ble_advertising XIAO").pattern == "XIAO"
    assert parse_expectation("gatt_service 0x180D").service_uuid == "0x180D"
    assert parse_expectation("socket_reachable").kind == ExpectationKind.SOCKET_REACHABLE


def test_parse_contract_with_headers_and_comments():
    text = """
    # the XIAO boot + heartbeat contract
    @id boot-check
    @target seeed_xiao_nrf52840_sense
    @receiver serial
    @timeout 12000

    contains BOOT_OK within_ms:3000
    no_timeout 1500
    field accel_x not_frozen
    """
    c = parse_contract(text)
    assert c.id == "boot-check"
    assert c.target == "seeed_xiao_nrf52840_sense"
    assert c.timeout_ms == 12000
    assert c.receivers[0].type == ReceiverKind.SERIAL
    assert [e.kind for e in c.expectations] == [
        ExpectationKind.CONTAINS, ExpectationKind.NO_TIMEOUT, ExpectationKind.FIELD_NOT_FROZEN,
    ]


def test_dsl_errors_are_honest_with_line_numbers():
    with pytest.raises(DSLError) as ei:
        parse_expectation("contains")          # missing pattern
    assert "needs a pattern" in str(ei.value)

    with pytest.raises(DSLError) as ei:
        parse_expectation("frobnicate X")       # unknown directive
    assert "unknown directive" in str(ei.value)

    with pytest.raises(DSLError) as ei:
        parse_expectation("count_min HB notanumber")
    assert "integer" in str(ei.value)

    with pytest.raises(DSLError):
        parse_contract("@target x\n# only comments + header, no expectations\n")


def test_dsl_output_is_assessable_end_to_end():
    # A DSL contract feeds straight into the engine + break-on-purpose.
    from evcide.models import ReceiverKind as RK, RuntimeEvent
    from evcide.verify import evaluate_events, overall_status
    from evcide.mutation import assess_contract

    c = parse_contract("contains BOOT_OK\nno_timeout 500", id="e2e")
    events = [
        RuntimeEvent(source=RK.SERIAL, timestamp_ms=200 * i, board_id="b",
                     stream_id="s", type="line", raw=("BOOT_OK" if i == 0 else "hb"))
        for i in range(5)
    ]
    assert overall_status(evaluate_events(c, events)) == "pass"
    assert assess_contract(c, events).meaningful is True
