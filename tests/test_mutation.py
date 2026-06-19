"""Tests for break-on-purpose / contract mutation testing (evcide.mutation).

All offline + deterministic: synthetic passing streams in, MutationReport out.
Proves the keystone — a contract that can't catch a deliberate break is flagged
as theater, and a contract that catches every break is certified meaningful.
"""
from __future__ import annotations

import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))

from evcide.models import (
    Expectation, ExpectationKind, ReceiverDef, ReceiverKind, RuntimeEvent, VerificationContract,
)
from evcide.mutation import assess_contract
from evcide.verify import evaluate_events, overall_status


# ---- helpers ----

def _ev(text="", t_ms=0, parsed=None) -> RuntimeEvent:
    return RuntimeEvent(
        source=ReceiverKind.SERIAL, timestamp_ms=t_ms,
        board_id="b", stream_id="s", type="line", raw=text, parsed=parsed,
    )


def _contract(expectations, cid="ctr") -> VerificationContract:
    return VerificationContract(
        id=cid, target="t", timeout_ms=10000,
        receivers=[ReceiverDef(type=ReceiverKind.SERIAL)], expectations=expectations,
    )


def _boot_and_motion_stream() -> list[RuntimeEvent]:
    # BOOT_OK once, then 10 varying accel_x samples (not frozen).
    evs = [_ev("BOOT_OK ready", 0)]
    evs += [_ev("imu", 100 * (i + 1), parsed={"accel_x": float(i)}) for i in range(10)]
    return evs


# ---- meaningful contracts kill their mutants ----

def test_meaningful_contract_kills_every_mutant():
    contract = _contract([
        Expectation(kind=ExpectationKind.CONTAINS, pattern="BOOT_OK"),
        Expectation(kind=ExpectationKind.FIELD_NOT_FROZEN, field="accel_x"),
    ])
    baseline = _boot_and_motion_stream()
    # sanity: the baseline actually passes
    assert overall_status(evaluate_events(contract, baseline)) == "pass"

    report = assess_contract(contract, baseline)
    assert report.baseline_passed is True
    assert report.survived == 0
    assert report.meaningful is True
    assert report.score == 1.0
    assert report.total_mutants == 3  # 2 targeted + 1 trivial empty-stream


def test_rate_contract_decimate_is_killed():
    contract = _contract([
        Expectation(kind=ExpectationKind.MESSAGE_RATE_HZ, min_rate_hz=5.0),
    ])
    # 11 events over 1000ms ≈ 10Hz → passes a 5Hz floor.
    baseline = [_ev("tick", 100 * i) for i in range(11)]
    assert overall_status(evaluate_events(contract, baseline)) == "pass"

    report = assess_contract(contract, baseline)
    assert report.meaningful is True
    assert report.survived == 0


# ---- theater contracts leave a surviving mutant ----

def test_unbounded_field_range_is_flagged_as_theater():
    # A FIELD_RANGE check with no min/max passes ANY numeric value — it tests nothing.
    contract = _contract([
        Expectation(kind=ExpectationKind.FIELD_RANGE, field="temp"),  # no value_min/value_max
    ])
    baseline = [_ev("t", 100 * i, parsed={"temp": 20.0}) for i in range(3)]
    assert overall_status(evaluate_events(contract, baseline)) == "pass"

    report = assess_contract(contract, baseline)
    assert report.baseline_passed is True
    assert report.meaningful is False
    assert report.survived >= 1
    survivor = next(s for s in report.survivors if s.mutator == "force_out_of_range")
    assert survivor.expectation_kind == "field_range"


def test_empty_contract_is_pure_theater():
    # No expectations → passes everything, including an empty stream.
    contract = _contract([])
    report = assess_contract(contract, [_ev("anything", 0)])
    assert report.baseline_passed is True
    assert report.total_mutants == 1            # only the trivial mutant
    assert report.meaningful is False
    assert report.survivors[0].mutator == "empty_stream"


# ---- guards ----

def test_non_passing_baseline_is_rejected():
    contract = _contract([Expectation(kind=ExpectationKind.CONTAINS, pattern="NEVER_PRINTED")])
    report = assess_contract(contract, [_ev("BOOT_OK", 0)])
    assert report.baseline_passed is False
    assert report.meaningful is False
    assert report.total_mutants == 0
    assert "baseline" in report.note.lower()


def test_unassessable_kind_is_noted_not_silently_dropped():
    # ROS_TOPIC_RATE_HZ has no evaluator → it settles inconclusive, so the baseline
    # can't PASS. The report must NAME it (no silent coverage drop), not just "failed".
    contract = _contract([
        Expectation(kind=ExpectationKind.CONTAINS, pattern="BOOT_OK"),
        Expectation(kind=ExpectationKind.ROS_TOPIC_RATE_HZ, min_rate_hz=10.0),
    ])
    report = assess_contract(contract, [_ev("BOOT_OK", 0)])
    assert report.baseline_passed is False
    assert report.total_mutants == 0
    assert "ros_topic_rate_hz" in report.note    # the unassessable kind is surfaced
    assert "baseline" in report.note.lower()


# ---- NO_TIMEOUT liveness check (wired evaluator + break-on-purpose) ----

def test_no_timeout_passes_on_steady_stream_fails_on_silence_gap():
    contract = _contract([Expectation(kind=ExpectationKind.NO_TIMEOUT, duration_ms=500)])
    steady = [_ev("hb", 200 * i) for i in range(6)]          # gaps of 200ms < 500ms
    assert overall_status(evaluate_events(contract, steady)) == "pass"
    hung = [_ev("hb", 0), _ev("hb", 50), _ev("hb", 9000)]    # 8950ms silence
    checks = evaluate_events(contract, hung)
    assert checks[0].status == "fail"
    assert "silent" in checks[0].message


def test_no_timeout_contract_is_meaningful_under_mutation():
    contract = _contract([Expectation(kind=ExpectationKind.NO_TIMEOUT, duration_ms=500)])
    steady = [_ev("hb", 200 * i) for i in range(6)]
    report = assess_contract(contract, steady)
    assert report.baseline_passed is True
    assert report.meaningful is True            # the injected silence-gap mutant is killed
    assert report.survived == 0


# ---- minimality: is every expectation load-bearing? (leave-one-out) ----

def test_minimal_contract_every_expectation_load_bearing():
    contract = _contract([
        Expectation(kind=ExpectationKind.CONTAINS, pattern="BOOT_OK"),
        Expectation(kind=ExpectationKind.FIELD_NOT_FROZEN, field="accel_x"),
    ])
    baseline = _boot_and_motion_stream()
    from evcide.mutation import assess_minimality
    report = assess_minimality(contract, baseline)
    assert report.baseline_passed is True
    assert report.minimal is True
    assert report.redundant == []
    assert all(c.load_bearing for c in report.expectations)


def test_redundant_expectation_is_flagged():
    # contains BOOT_OK and count_min BOOT_OK 1 both catch dropping BOOT_OK — neither
    # uniquely. Both should be flagged redundant (no unique kill).
    contract = _contract([
        Expectation(kind=ExpectationKind.CONTAINS, pattern="BOOT_OK"),
        Expectation(kind=ExpectationKind.COUNT_MIN, pattern="BOOT_OK", count=1),
    ])
    baseline = [_ev("BOOT_OK", 0), _ev("hb", 200), _ev("hb", 400)]
    from evcide.mutation import assess_minimality
    report = assess_minimality(contract, baseline)
    assert report.baseline_passed is True
    assert report.minimal is False
    assert set(report.redundant) == {0, 1}
    assert "no unique kill" in report.note


def test_minimality_rejects_non_passing_baseline():
    contract = _contract([Expectation(kind=ExpectationKind.CONTAINS, pattern="NEVER")])
    from evcide.mutation import assess_minimality
    report = assess_minimality(contract, [_ev("BOOT_OK", 0)])
    assert report.baseline_passed is False
    assert report.minimal is False
