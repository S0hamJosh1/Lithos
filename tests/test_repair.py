"""Tests for repair loop v2 (evcide.repair).

Every assertion runs with no hardware and no LLM: synthetic VerificationResults
in, deterministic hints + RepairRequest out. The model call is the only thing
that isn't tested here, and that boundary is asserted to fail honestly.
"""
from __future__ import annotations

import asyncio
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))

import pytest

from evcide import repair
from evcide.models import (
    CheckResult,
    Expectation,
    ExpectationKind,
    FixProposal,
    ReceiverKind,
    RepairRequest,
    RuntimeEvent,
    RuntimeEvidence,
    VerificationResult,
)


# ---- helpers ----

def _check(kind: ExpectationKind, status="fail", message="", actual=None, **exp_kw) -> CheckResult:
    return CheckResult(
        expectation=Expectation(kind=kind, **exp_kw),
        status=status,
        actual=actual or {},
        message=message,
    )


def _result(checks, status="fail", failure_classification=None, evidence=None) -> VerificationResult:
    return VerificationResult(
        status=status,
        contract_id="ctr-test",
        started_at="2026-01-01T00:00:00+00:00",
        ended_at="2026-01-01T00:00:12+00:00",
        checks=checks,
        evidence=evidence if evidence is not None else [],
        failure_classification=failure_classification,
    )


def _evidence(events, receiver=ReceiverKind.SERIAL) -> RuntimeEvidence:
    return RuntimeEvidence(
        stream_id="strm-test", receiver=receiver,
        event_count=len(events), duration_ms=12000,
        first_event_at_ms=0, last_event_at_ms=12000,
        sample_events=events,
    )


def _ev(text: str, t_ms: int = 0) -> RuntimeEvent:
    return RuntimeEvent(
        source=ReceiverKind.SERIAL, timestamp_ms=t_ms,
        board_id="b", stream_id="strm-test", type="line", raw=text,
    )


# ---- per-check classification ----

def test_classify_check_boot_vs_generic_token():
    boot = _check(ExpectationKind.CONTAINS, pattern="BOOT_OK")
    other = _check(ExpectationKind.CONTAINS, pattern="READY")
    assert repair.classify_check(boot) == "boot_msg_missing"
    assert repair.classify_check(other) == "expected_token_missing"


def test_classify_check_rate_direction_from_message():
    lo = _check(ExpectationKind.MESSAGE_RATE_HZ, message="rate 5.00Hz below min 10")
    hi = _check(ExpectationKind.MESSAGE_RATE_HZ, message="rate 90.0Hz above max 60")
    band = _check(ExpectationKind.MESSAGE_RATE_HZ, message="rate 5.00Hz outside band [9..11]")
    assert repair.classify_check(lo) == "rate_too_low"
    assert repair.classify_check(hi) == "rate_too_high"
    assert repair.classify_check(band) == "rate_out_of_band"


def test_classify_check_field_and_sensor_kinds():
    assert repair.classify_check(_check(ExpectationKind.FIELD_NOT_FROZEN, field="accel_x")) == "field_frozen"
    assert repair.classify_check(_check(ExpectationKind.NO_NAN, field="gx")) == "field_nan"
    assert repair.classify_check(_check(ExpectationKind.FIELD_RANGE, field="t")) == "field_out_of_range"
    assert repair.classify_check(_check(ExpectationKind.SOCKET_REACHABLE)) == "socket_unreachable"


def test_classify_check_ignores_passing_check():
    assert repair.classify_check(_check(ExpectationKind.CONTAINS, status="pass", pattern="BOOT_OK")) is None


# ---- hint synthesis ----

def test_no_data_class_short_circuits_to_single_hint():
    # Two failing checks, but the whole-stream cause should win with one hint.
    checks = [
        _check(ExpectationKind.CONTAINS, pattern="BOOT_OK"),
        _check(ExpectationKind.FIELD_NOT_FROZEN, field="accel_x"),
    ]
    res = _result(checks, failure_classification="no_serial_data")
    hints = repair.build_repair_hints(res, framework="zephyr")
    assert len(hints) == 1
    assert hints[0].classification == "no_serial_data"
    # Zephyr overlay supplies the prj.conf settings (subsumes nrf52 v1 behavior).
    assert hints[0].target_settings == {"prj.conf": {"CONFIG_UART_CONSOLE": "y", "CONFIG_USB_DEVICE_STACK": "y"}}


def test_per_check_hints_deduped_and_severity_ordered():
    checks = [
        _check(ExpectationKind.MESSAGE_RATE_HZ, message="rate below min"),   # warn
        _check(ExpectationKind.CONTAINS, pattern="BOOT_OK"),                 # block
        _check(ExpectationKind.CONTAINS, pattern="BOOT_OK"),                 # dup block
    ]
    hints = repair.build_repair_hints(_result(checks))
    classes = [h.classification for h in hints]
    assert classes == ["boot_msg_missing", "rate_too_low"]  # block before warn, deduped
    assert hints[0].severity == "block" and hints[1].severity == "warn"


def test_generic_framework_has_no_settings_overlay():
    res = _result([], failure_classification="no_serial_data")
    hints = repair.build_repair_hints(res)  # no framework
    assert hints[0].classification == "no_serial_data"
    assert hints[0].target_settings == {}


def test_passing_result_yields_no_hints():
    assert repair.build_repair_hints(_result([], status="pass")) == []


# ---- repair request (the LLM-ready prompt) ----

def test_build_repair_request_renders_actionable_prompt():
    checks = [
        _check(ExpectationKind.CONTAINS, pattern="BOOT_OK",
               message="not satisfied within within_ms deadline 3000ms",
               actual={"pass_at_ms": None}),
        _check(ExpectationKind.FIELD_NOT_FROZEN, field="accel_x",
               message="accel_x frozen at 0.0 for 8 consecutive samples"),
    ]
    ev = _evidence([_ev("BOOT slow"), _ev("accel_x=0.0")])
    res = _result(checks, evidence=[ev])
    req = repair.build_repair_request(res, framework="zephyr")

    assert isinstance(req, RepairRequest)
    assert req.contract_id == "ctr-test"
    assert "boot_msg_missing" in req.failure_classifications
    assert "field_frozen" in req.failure_classifications
    assert len(req.failed_checks) == 2
    assert "src/main.c" in req.target_files
    # prompt must carry the moat-critical pieces
    assert "ctr-test" in req.prompt
    assert "BOOT_OK" in req.prompt
    assert "accel_x frozen" in req.prompt
    assert "events over 12000ms" in req.prompt          # evidence digest
    assert "unified diff" in req.prompt.lower()         # actionable instruction


def test_repair_request_reuses_prepopulated_hints():
    # If repair_hints already set (e.g. by an adapter), the request reuses them.
    res = _result([_check(ExpectationKind.CONTAINS, pattern="BOOT_OK")])
    res.repair_hints = repair.build_repair_hints(res, framework="zephyr")
    req = repair.build_repair_request(res)
    assert [h.classification for h in req.hints] == [h.classification for h in res.repair_hints]


def test_inconclusive_only_result_still_builds_request():
    checks = [_check(ExpectationKind.CONTAINS, status="inconclusive", pattern="BOOT_OK")]
    res = _result(checks, status="inconclusive")
    req = repair.build_repair_request(res)
    assert len(req.failed_checks) == 1  # falls back to inconclusive checks
    assert req.prompt


# ---- fix provider boundary ----

def test_null_fix_provider_refuses_honestly():
    req = RepairRequest(contract_id="c", overall_status="fail")
    with pytest.raises(NotImplementedError):
        asyncio.run(repair.NullFixProvider().propose_fix(req))


def test_attempt_repair_drives_injected_provider():
    class CannedProvider:
        async def propose_fix(self, request: RepairRequest) -> FixProposal:
            return FixProposal(applied=True, diff="--- a\n+++ b\n", confidence=0.9)

    res = _result([_check(ExpectationKind.CONTAINS, pattern="BOOT_OK")])
    proposal = asyncio.run(repair.attempt_repair(res, CannedProvider(), framework="zephyr"))
    assert proposal.applied is True
    assert proposal.confidence == 0.9


# ---- end-to-end: the engine attaches a repair_request on failure ----

@pytest.mark.asyncio
async def test_run_verification_attaches_repair_request_on_failure(monkeypatch):
    from evcide import receivers
    from evcide.models import OutputConfig, OutputSession, VerificationContract, ReceiverDef, now_ms
    from evcide.verify import run_verification

    async def stream(session, stop_event):
        # Board never prints BOOT_OK → the contains check fails.
        for ev in [_ev("noise 1", 100), _ev("noise 2", 1100)]:
            if stop_event.is_set():
                return
            yield ev
            await asyncio.sleep(0)

    monkeypatch.setattr(receivers, "get_receiver_stream", lambda kind: stream)
    session = OutputSession(
        stream_id="strm-test", receiver=ReceiverKind.SERIAL, board_id="b",
        started_at_ms=now_ms(),
        config=OutputConfig(receiver=ReceiverKind.SERIAL, serial_port="/dev/null", serial_baud=115200),
    )
    contract = VerificationContract(
        id="ctr-e2e", target="seeed_xiao_nrf52840_sense", timeout_ms=300,
        receivers=[ReceiverDef(type=ReceiverKind.SERIAL)],
        expectations=[Expectation(kind=ExpectationKind.CONTAINS, pattern="BOOT_OK", within_ms=200)],
    )
    result = await run_verification(session, contract)  # no adapter → engine uses shared layer

    assert result.status == "fail"
    assert result.repair_hints  # generic hints present
    assert result.repair_request is not None
    assert "BOOT_OK" in result.repair_request.prompt
    assert "boot_msg_missing" in result.repair_request.failure_classifications
