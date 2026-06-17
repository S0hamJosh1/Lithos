"""Verification Engine — PDF Sections 21-22.

Converts a VerificationContract (declarative JSON expectations) into a
measurable PASS / FAIL / INCONCLUSIVE result against a live receiver stream.

This is the moat. Flashing is solved; verifying that the firmware actually
did the right thing on real hardware is not.
"""
from __future__ import annotations

import asyncio
import math
import time
from dataclasses import dataclass, field
from typing import Any

from .models import (
    CheckResult,
    Expectation,
    ExpectationKind,
    OutputSession,
    ReceiverKind,
    RepairHint,
    RuntimeEvent,
    RuntimeEvidence,
    VerificationContract,
    VerificationResult,
    iso_now,
    now_ms,
)
from . import receivers


# ============ Live evaluator state ============

@dataclass
class ExpectationState:
    """Per-expectation rolling state that checks update during streaming."""
    expectation: Expectation
    pass_seen: bool = False
    fail_seen: bool = False
    actual: dict[str, Any] = field(default_factory=dict)
    evidence_event_ids: list[int] = field(default_factory=list)
    message: str = ""

    def settle(self, default_status: str = "inconclusive") -> CheckResult:
        if self.pass_seen and not self.fail_seen:
            status = "pass"
        elif self.fail_seen:
            status = "fail"
        else:
            status = default_status
        return CheckResult(
            expectation=self.expectation,
            status=status,
            actual=self.actual,
            evidence_event_ids=self.evidence_event_ids,
            message=self.message,
        )


# ============ Check evaluators ============

def _ev_text(ev: RuntimeEvent) -> str:
    if isinstance(ev.raw, str):
        return ev.raw
    if isinstance(ev.raw, (bytes, bytearray)):
        return bytes(ev.raw).decode("utf-8", errors="replace")
    return ""


def _ev_field(ev: RuntimeEvent, field_name: str) -> Any:
    if ev.parsed and field_name in ev.parsed:
        return ev.parsed[field_name]
    return None


def _update_contains(st: ExpectationState, ev: RuntimeEvent, idx: int) -> None:
    pat = st.expectation.pattern or ""
    text = _ev_text(ev)
    if pat and pat in text:
        st.pass_seen = True
        st.evidence_event_ids.append(idx)
        st.actual = {"first_seen_at_ms": ev.timestamp_ms}


def _update_not_contains(st: ExpectationState, ev: RuntimeEvent, idx: int) -> None:
    pat = st.expectation.pattern or ""
    text = _ev_text(ev)
    if pat and pat in text:
        st.fail_seen = True
        st.evidence_event_ids.append(idx)
        st.message = f"forbidden token {pat!r} appeared"


def _update_count(st: ExpectationState, ev: RuntimeEvent, idx: int) -> None:
    pat = st.expectation.pattern or ""
    text = _ev_text(ev)
    if pat and pat in text:
        st.actual["count"] = st.actual.get("count", 0) + 1
        st.evidence_event_ids.append(idx)
        kind = st.expectation.kind
        target = st.expectation.count or 0
        cur = st.actual["count"]
        if kind == ExpectationKind.COUNT_MIN and cur >= target:
            st.pass_seen = True
        if kind == ExpectationKind.COUNT_EXACT and cur == target:
            st.pass_seen = True
        if kind == ExpectationKind.COUNT_MAX and cur > target:
            st.fail_seen = True
            st.message = f"count exceeded max {target}; observed {cur}"


def _update_sequence(st: ExpectationState, ev: RuntimeEvent, idx: int) -> None:
    seq = st.expectation.sequence or []
    if not seq:
        return
    text = _ev_text(ev)
    step = st.actual.get("step", 0)
    if step < len(seq) and seq[step] in text:
        step += 1
        st.actual["step"] = step
        st.evidence_event_ids.append(idx)
        if step >= len(seq):
            st.pass_seen = True


def _update_message_rate(st: ExpectationState, ev: RuntimeEvent, idx: int) -> None:
    """Track rolling sample rate per the receiver. Pass when rate is in band over
    the contract duration window; fail when window-end is out of band.
    """
    st.actual.setdefault("count", 0)
    st.actual.setdefault("first_ms", ev.timestamp_ms)
    st.actual["count"] += 1
    st.actual["last_ms"] = ev.timestamp_ms
    st.evidence_event_ids.append(idx)


def _settle_rate(st: ExpectationState) -> None:
    cnt = st.actual.get("count", 0)
    first = st.actual.get("first_ms")
    last = st.actual.get("last_ms")
    if cnt < 2 or first is None or last is None or last == first:
        st.message = f"insufficient samples to compute rate ({cnt})"
        return
    span_s = (last - first) / 1000.0
    rate = cnt / span_s
    st.actual["rate_hz"] = round(rate, 3)
    exp = st.expectation
    if exp.expected_rate_hz is not None and exp.rate_tolerance_pct is not None:
        lo = exp.expected_rate_hz * (1 - exp.rate_tolerance_pct / 100)
        hi = exp.expected_rate_hz * (1 + exp.rate_tolerance_pct / 100)
        if lo <= rate <= hi:
            st.pass_seen = True
        else:
            st.fail_seen = True
            st.message = f"rate {rate:.2f}Hz outside band [{lo:.2f}..{hi:.2f}]"
        return
    if exp.min_rate_hz is not None and rate < exp.min_rate_hz:
        st.fail_seen = True
        st.message = f"rate {rate:.2f}Hz below min {exp.min_rate_hz}"
        return
    if exp.max_rate_hz is not None and rate > exp.max_rate_hz:
        st.fail_seen = True
        st.message = f"rate {rate:.2f}Hz above max {exp.max_rate_hz}"
        return
    st.pass_seen = True


def _apply_within_ms(st: ExpectationState) -> None:
    """Downgrade a check that only passed after its within_ms deadline.

    Timing is relative to the first event on the stream (≈ boot for a
    freshly-opened receiver). A deadline that is specified but never met — even
    by absence of the event — is a FAIL, not inconclusive: the contract
    *required* the behavior within the window. Rate checks are exempt; their
    verdict comes from _settle_rate and has no per-event pass timestamp.
    """
    exp = st.expectation
    deadline = exp.within_ms
    if deadline is None or exp.kind == ExpectationKind.MESSAGE_RATE_HZ:
        return
    pass_at = st.actual.get("pass_at_ms")
    if st.pass_seen and pass_at is not None and pass_at > deadline:
        st.pass_seen = False
        st.fail_seen = True
        st.message = f"satisfied at {pass_at}ms, after within_ms deadline {deadline}ms"
    elif not st.pass_seen and not st.fail_seen:
        st.fail_seen = True
        st.message = f"not satisfied within within_ms deadline {deadline}ms"


def _update_field_present(st: ExpectationState, ev: RuntimeEvent, idx: int) -> None:
    fname = st.expectation.field
    if fname and _ev_field(ev, fname) is not None:
        st.pass_seen = True
        st.evidence_event_ids.append(idx)


def _update_field_range(st: ExpectationState, ev: RuntimeEvent, idx: int) -> None:
    fname = st.expectation.field
    if not fname:
        return
    v = _ev_field(ev, fname)
    if not isinstance(v, (int, float)):
        return
    lo, hi = st.expectation.value_min, st.expectation.value_max
    st.evidence_event_ids.append(idx)
    if lo is not None and v < lo:
        st.fail_seen = True
        st.message = f"{fname}={v} below min {lo}"
        return
    if hi is not None and v > hi:
        st.fail_seen = True
        st.message = f"{fname}={v} above max {hi}"
        return
    st.pass_seen = True


def _update_field_not_frozen(st: ExpectationState, ev: RuntimeEvent, idx: int) -> None:
    fname = st.expectation.field
    if not fname:
        return
    v = _ev_field(ev, fname)
    if v is None:
        return
    hist = st.actual.setdefault("history", [])
    hist.append(v)
    st.evidence_event_ids.append(idx)
    if len(hist) >= 8:
        if all(x == hist[0] for x in hist[-8:]):
            st.fail_seen = True
            st.message = f"{fname} frozen at {hist[0]} for 8 consecutive samples"
        else:
            st.pass_seen = True


def _update_no_nan(st: ExpectationState, ev: RuntimeEvent, idx: int) -> None:
    fname = st.expectation.field
    if not fname or not ev.parsed:
        return
    v = ev.parsed.get(fname)
    if isinstance(v, float) and math.isnan(v):
        st.fail_seen = True
        st.evidence_event_ids.append(idx)
        st.message = f"{fname} produced NaN"
        return
    if isinstance(v, (int, float)):
        st.pass_seen = True


_UPDATERS = {
    ExpectationKind.CONTAINS: _update_contains,
    ExpectationKind.NOT_CONTAINS: _update_not_contains,
    ExpectationKind.COUNT_MIN: _update_count,
    ExpectationKind.COUNT_MAX: _update_count,
    ExpectationKind.COUNT_EXACT: _update_count,
    ExpectationKind.SEQUENCE: _update_sequence,
    ExpectationKind.MESSAGE_RATE_HZ: _update_message_rate,
    ExpectationKind.FIELD_PRESENT: _update_field_present,
    ExpectationKind.FIELD_RANGE: _update_field_range,
    ExpectationKind.FIELD_NOT_FROZEN: _update_field_not_frozen,
    ExpectationKind.NO_NAN: _update_no_nan,
}


# ============ Orchestrator ============

async def run_verification(
    session: OutputSession,
    contract: VerificationContract,
    adapter=None,
    sink=None,
) -> VerificationResult:
    """Open the session's receiver, evaluate every expectation against the
    live stream, settle on timeout or first-failure-and-cannot-recover.

    If `sink` is given (an async callable taking a dict), each RuntimeEvent is
    published as ``{"type": "event", ...}`` while streaming and the final
    VerificationResult as ``{"type": "result", ...}``, so the frontend can watch
    the run live over a WebSocket. The HTTP response still returns the result.
    """
    started_at = iso_now()
    start_ms = now_ms()

    states = [ExpectationState(expectation=e) for e in contract.expectations]
    events_seen: list[RuntimeEvent] = []
    sample_events: list[RuntimeEvent] = []
    first_ev_ms: int | None = None
    last_ev_ms: int | None = None

    stop_event = asyncio.Event()
    stream_fn = receivers.get_receiver_stream(session.receiver)

    async def _consume():
        nonlocal first_ev_ms, last_ev_ms
        try:
            agen = stream_fn(session, stop_event)
            async for ev in agen:
                idx = len(events_seen)
                events_seen.append(ev)
                if first_ev_ms is None:
                    first_ev_ms = ev.timestamp_ms
                last_ev_ms = ev.timestamp_ms
                if len(sample_events) < 12:
                    sample_events.append(ev)
                if sink is not None:
                    await sink({"type": "event", "data": ev.model_dump(mode="json")})
                elapsed = ev.timestamp_ms - first_ev_ms
                for st in states:
                    if st.pass_seen and st.fail_seen:
                        continue
                    updater = _UPDATERS.get(st.expectation.kind)
                    if updater is None:
                        continue
                    exp = st.expectation
                    # after_ms: ignore warm-up events before the window opens.
                    if exp.after_ms is not None and elapsed < exp.after_ms:
                        continue
                    was_pass = st.pass_seen
                    updater(st, ev, idx)
                    # Record when the check first reached PASS (for within_ms).
                    if st.pass_seen and not was_pass and "pass_at_ms" not in st.actual:
                        st.actual["pass_at_ms"] = elapsed
                # short-circuit if every expectation already settled with pass
                if all(s.pass_seen and not s.fail_seen for s in states):
                    break
        except NotImplementedError as e:
            # surface receiver-not-implemented as an inconclusive verification
            for st in states:
                st.message = str(e)
        except Exception as e:
            for st in states:
                st.message = f"receiver error: {e}"

    try:
        await asyncio.wait_for(_consume(), timeout=contract.timeout_ms / 1000.0)
    except asyncio.TimeoutError:
        pass
    finally:
        stop_event.set()

    # Settle rate expectations now that streaming stopped.
    for st in states:
        if st.expectation.kind == ExpectationKind.MESSAGE_RATE_HZ:
            _settle_rate(st)

    # Enforce within_ms deadlines now that we know when each check passed.
    for st in states:
        _apply_within_ms(st)

    checks = [st.settle() for st in states]
    overall = (
        "pass" if all(c.status == "pass" for c in checks) else
        "fail" if any(c.status == "fail" for c in checks) else
        "inconclusive"
    )

    failure_class = _classify_failure(overall, checks, events_seen, contract)
    evidence = [
        RuntimeEvidence(
            stream_id=session.stream_id,
            receiver=session.receiver,
            event_count=len(events_seen),
            duration_ms=(last_ev_ms - first_ev_ms) if (first_ev_ms and last_ev_ms) else 0,
            first_event_at_ms=first_ev_ms,
            last_event_at_ms=last_ev_ms,
            sample_events=sample_events,
        )
    ]

    result = VerificationResult(
        status=overall,
        contract_id=contract.id,
        started_at=started_at,
        ended_at=iso_now(),
        checks=checks,
        evidence=evidence,
        failure_classification=failure_class,
        repair_hints=[],
        agent_summary=_summarize(overall, checks, events_seen, failure_class),
    )

    if adapter is not None and overall != "pass":
        result.repair_hints = await adapter.repair_hints(result)

    if sink is not None:
        await sink({"type": "result", "data": result.model_dump(mode="json")})

    return result


def _classify_failure(
    overall: str,
    checks: list[CheckResult],
    events: list[RuntimeEvent],
    contract: VerificationContract,
) -> str | None:
    if overall == "pass":
        return None
    if not events:
        # Receiver got nothing at all in the window.
        kind = contract.receivers[0].type if contract.receivers else None
        if kind == ReceiverKind.SERIAL:
            return "no_serial_data"
        if kind == ReceiverKind.BLE:
            return "ble_not_advertising"
        return "no_runtime_data"
    # Specific-shape failures
    for c in checks:
        if c.status != "fail":
            continue
        k = c.expectation.kind
        msg = c.message or ""
        if k == ExpectationKind.CONTAINS and "BOOT" in (c.expectation.pattern or ""):
            return "boot_msg_missing"
        if k == ExpectationKind.MESSAGE_RATE_HZ:
            if "below" in msg:
                return "rate_too_low"
            if "above" in msg:
                return "rate_too_high"
        if k == ExpectationKind.FIELD_NOT_FROZEN:
            return "imu_frozen"
        if k == ExpectationKind.NO_NAN:
            return "field_nan"
    return "expectation_unmet"


def _summarize(
    overall: str,
    checks: list[CheckResult],
    events: list[RuntimeEvent],
    failure_class: str | None,
) -> str:
    pass_n = sum(1 for c in checks if c.status == "pass")
    fail_n = sum(1 for c in checks if c.status == "fail")
    inc_n = sum(1 for c in checks if c.status == "inconclusive")
    head = (
        "PASS - every expectation satisfied" if overall == "pass" else
        f"FAIL - {fail_n} failed" if overall == "fail" else
        "INCONCLUSIVE - no expectation reached a verdict"
    )
    tail = f" ({pass_n} pass, {fail_n} fail, {inc_n} inconclusive over {len(events)} events"
    if failure_class:
        tail += f", classification={failure_class}"
    tail += ")"
    return head + tail
