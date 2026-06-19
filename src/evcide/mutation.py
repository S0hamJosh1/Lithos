"""Break-on-purpose for verification contracts — PDF Section 24.

The confidence loop's keystone, applied to runtime verification:

    "Inject one deliberate bug and confirm a test goes red. If nothing fails,
     the test is theater — fix it before trusting anything."

A vibe-coded VerificationContract can pass *vacuously* — `contains BOOT_OK` is
satisfied by a board stuck in a boot loop, a `field_range` check passes if the
field is simply absent. The only way to know a contract means something is to
break the behavior it guards and confirm the contract notices.

We mutate the **evidence stream**, not the firmware: take a baseline stream that
PASSES the contract, apply one targeted mutation per expectation (drop the boot
token, freeze the field, decimate the rate, …), re-evaluate offline, and check
that the targeted expectation stops passing. A mutation the contract fails to
catch is a *surviving mutant* — the theater signal. No hardware, no rebuild: the
same deterministic discipline as the rest of the engine.
"""
from __future__ import annotations

import math
from dataclasses import dataclass
from typing import Callable

from .models import (
    Expectation,
    ExpectationCoverage,
    ExpectationKind,
    MinimalityReport,
    MutationReport,
    RuntimeEvent,
    SurvivingMutant,
    VerificationContract,
)
from .verify import _UPDATERS, evaluate_events, overall_status

# Kinds the engine actually evaluates (have an updater); others can't be assessed.
_ASSESSABLE = set(_UPDATERS.keys())


# ============ Mutators ============

@dataclass
class Mutator:
    """A named transformation of a passing event stream into a broken one."""
    name: str
    detail: str
    fn: Callable[[list[RuntimeEvent]], list[RuntimeEvent]]


def _text(ev: RuntimeEvent) -> str:
    if isinstance(ev.raw, str):
        return ev.raw
    if isinstance(ev.raw, (bytes, bytearray)):
        return bytes(ev.raw).decode("utf-8", errors="replace")
    return ev.type


def _with_parsed(ev: RuntimeEvent, field: str, value: object) -> RuntimeEvent:
    parsed = dict(ev.parsed or {})
    parsed[field] = value
    return ev.model_copy(update={"parsed": parsed})


def _without_parsed(ev: RuntimeEvent, field: str) -> RuntimeEvent:
    parsed = dict(ev.parsed or {})
    parsed.pop(field, None)
    return ev.model_copy(update={"parsed": parsed})


def _drop_tokens(patterns: list[str]) -> Callable[[list[RuntimeEvent]], list[RuntimeEvent]]:
    pats = [p for p in patterns if p]

    def fn(events: list[RuntimeEvent]) -> list[RuntimeEvent]:
        return [e for e in events if not any(p in _text(e) for p in pats)]

    return fn


def _inject_token(pattern: str) -> Callable[[list[RuntimeEvent]], list[RuntimeEvent]]:
    def fn(events: list[RuntimeEvent]) -> list[RuntimeEvent]:
        if not events:
            return events
        last = events[-1]
        extra = last.model_copy(update={"raw": f"{pattern} (injected)", "parsed": None})
        return [*events, extra]

    return fn


def _flood_token(pattern: str, n: int = 50) -> Callable[[list[RuntimeEvent]], list[RuntimeEvent]]:
    def fn(events: list[RuntimeEvent]) -> list[RuntimeEvent]:
        if not events:
            return events
        last = events[-1]
        extra = [last.model_copy(update={"raw": f"{pattern} {i}", "parsed": None}) for i in range(n)]
        return [*events, *extra]

    return fn


def _freeze_field(field: str) -> Callable[[list[RuntimeEvent]], list[RuntimeEvent]]:
    def fn(events: list[RuntimeEvent]) -> list[RuntimeEvent]:
        return [_with_parsed(e, field, 0.0) if (e.parsed and field in e.parsed) else e
                for e in events]

    return fn


def _inject_nan(field: str) -> Callable[[list[RuntimeEvent]], list[RuntimeEvent]]:
    def fn(events: list[RuntimeEvent]) -> list[RuntimeEvent]:
        out, done = [], False
        for e in events:
            if not done and e.parsed and field in e.parsed:
                out.append(_with_parsed(e, field, math.nan))
                done = True
            else:
                out.append(e)
        return out

    return fn


def _force_out_of_range(field: str, lo: float | None, hi: float | None):
    def fn(events: list[RuntimeEvent]) -> list[RuntimeEvent]:
        bad = (hi + 1.0e6) if hi is not None else ((lo - 1.0e6) if lo is not None else 1.0e9)
        return [_with_parsed(e, field, bad) if (e.parsed and field in e.parsed) else e
                for e in events]

    return fn


def _strip_field(field: str) -> Callable[[list[RuntimeEvent]], list[RuntimeEvent]]:
    def fn(events: list[RuntimeEvent]) -> list[RuntimeEvent]:
        return [_without_parsed(e, field) for e in events]

    return fn


def _decimate() -> Callable[[list[RuntimeEvent]], list[RuntimeEvent]]:
    """Keep only the first and last event — count collapses, span (and thus the
    window) is preserved, so the measured rate plummets below any real floor."""
    def fn(events: list[RuntimeEvent]) -> list[RuntimeEvent]:
        if len(events) <= 2:
            return events[:1]
        return [events[0], events[-1]]

    return fn


def _densify(n: int = 200) -> Callable[[list[RuntimeEvent]], list[RuntimeEvent]]:
    """Pack many events into the same span — rate spikes above any ceiling."""
    def fn(events: list[RuntimeEvent]) -> list[RuntimeEvent]:
        if len(events) < 2:
            return events
        first, last = events[0], events[-1]
        span = max(1, last.timestamp_ms - first.timestamp_ms)
        return [first.model_copy(update={"timestamp_ms": first.timestamp_ms + (span * i) // n})
                for i in range(n)] + [last]

    return fn


def _inject_silence_gap(delta_ms: int) -> Callable[[list[RuntimeEvent]], list[RuntimeEvent]]:
    """Push every event after the first later by delta_ms — opens a silent gap
    right after boot, as if the board hung."""
    def fn(events: list[RuntimeEvent]) -> list[RuntimeEvent]:
        if len(events) < 2:
            return events
        first, rest = events[0], events[1:]
        return [first] + [e.model_copy(update={"timestamp_ms": e.timestamp_ms + delta_ms}) for e in rest]

    return fn


def _drop_type(ev_type: str) -> Callable[[list[RuntimeEvent]], list[RuntimeEvent]]:
    def fn(events: list[RuntimeEvent]) -> list[RuntimeEvent]:
        return [e for e in events if e.type != ev_type]

    return fn


def _strip_service_uuid() -> Callable[[list[RuntimeEvent]], list[RuntimeEvent]]:
    def fn(events: list[RuntimeEvent]) -> list[RuntimeEvent]:
        out = []
        for e in events:
            p = dict(e.parsed or {})
            p.pop("service_uuid", None)
            p.pop("service_uuids", None)
            out.append(e.model_copy(update={"parsed": p}))
        return out

    return fn


def _make_unreachable() -> Callable[[list[RuntimeEvent]], list[RuntimeEvent]]:
    def fn(events: list[RuntimeEvent]) -> list[RuntimeEvent]:
        if not events:
            return events
        proto = events[0]
        return [proto.model_copy(update={"type": "unreachable", "raw": "connection refused"})]

    return fn


def _mutators_for(exp: Expectation) -> list[Mutator]:
    """The targeted mutation(s) that *should* break this one expectation."""
    k = exp.kind
    if k == ExpectationKind.CONTAINS:
        return [Mutator("drop_token", f"removed every event containing {exp.pattern!r}",
                        _drop_tokens([exp.pattern or ""]))]
    if k in (ExpectationKind.COUNT_MIN, ExpectationKind.COUNT_EXACT):
        return [Mutator("drop_token", f"removed every event containing {exp.pattern!r}",
                        _drop_tokens([exp.pattern or ""]))]
    if k == ExpectationKind.COUNT_MAX:
        return [Mutator("flood_token", f"flooded the stream with {exp.pattern!r}",
                        _flood_token(exp.pattern or ""))]
    if k == ExpectationKind.SEQUENCE:
        return [Mutator("drop_sequence", "removed events for every sequence token",
                        _drop_tokens(list(exp.sequence or [])))]
    if k == ExpectationKind.NOT_CONTAINS:
        return [Mutator("inject_forbidden", f"injected the forbidden token {exp.pattern!r}",
                        _inject_token(exp.pattern or "X"))]
    if k in (ExpectationKind.MESSAGE_RATE_HZ, ExpectationKind.ROS_TOPIC_RATE_HZ):
        if exp.max_rate_hz is not None and exp.min_rate_hz is None and exp.expected_rate_hz is None:
            return [Mutator("densify", "packed events to spike the rate above the ceiling", _densify())]
        return [Mutator("decimate", "dropped events to collapse the rate below the floor", _decimate())]
    if k == ExpectationKind.FIELD_PRESENT:
        return [Mutator("strip_field", f"removed field {exp.field!r} from every event",
                        _strip_field(exp.field or ""))]
    if k == ExpectationKind.FIELD_RANGE:
        return [Mutator("force_out_of_range", f"forced {exp.field!r} outside [{exp.value_min}, {exp.value_max}]",
                        _force_out_of_range(exp.field or "", exp.value_min, exp.value_max))]
    if k == ExpectationKind.FIELD_NOT_FROZEN:
        return [Mutator("freeze_field", f"froze {exp.field!r} to a constant", _freeze_field(exp.field or ""))]
    if k == ExpectationKind.NO_NAN:
        return [Mutator("inject_nan", f"set {exp.field!r} to NaN in one event", _inject_nan(exp.field or ""))]
    if k == ExpectationKind.NO_TIMEOUT:
        gap = (exp.duration_ms or 1000) * 10 + 10000
        return [Mutator("inject_silence_gap", f"opened a silent gap > {exp.duration_ms}ms (board hang)",
                        _inject_silence_gap(gap))]
    if k == ExpectationKind.BLE_ADVERTISING:
        return [Mutator("drop_advertisements", "removed every advertisement event", _drop_type("advertisement"))]
    if k == ExpectationKind.GATT_SERVICE_PRESENT:
        return [Mutator("strip_service_uuid", "removed the advertised service UUID", _strip_service_uuid())]
    if k == ExpectationKind.SOCKET_REACHABLE:
        return [Mutator("make_unreachable", "replaced the stream with an unreachable event", _make_unreachable())]
    return []


# ============ Contract assessment ============

def assess_contract(
    contract: VerificationContract, baseline_events: list[RuntimeEvent]
) -> MutationReport:
    """Break-on-purpose: is this contract meaningful, or theater?

    Requires a baseline stream that PASSES the contract (you can't measure whether
    a check catches breaks if it isn't green to begin with). Then, per expectation,
    apply the mutation that should break it and confirm the *targeted* expectation
    stops passing. Plus one global trivial mutant: an empty stream must not pass.
    """
    # Surface expectations the engine can't evaluate up front — a contract that
    # uses one can never PASS a baseline, so this is also usually *why* it didn't.
    unassessable = sorted({e.kind.value for e in contract.expectations if e.kind not in _ASSESSABLE})
    note = f"not assessed (no evaluator): {', '.join(unassessable)}" if unassessable else ""

    baseline_checks = evaluate_events(contract, baseline_events)
    if overall_status(baseline_checks) != "pass":
        tail = "baseline stream did not PASS; provide a passing stream before assessing."
        return MutationReport(
            contract_id=contract.id, baseline_passed=False,
            total_mutants=0, killed=0, survived=0, score=0.0, meaningful=False,
            note=f"{note}; {tail}" if note else tail,
        )

    survivors: list[SurvivingMutant] = []
    total = 0
    killed = 0

    for idx, exp in enumerate(contract.expectations):
        if exp.kind not in _ASSESSABLE:
            continue
        for mut in _mutators_for(exp):
            total += 1
            mutant = mut.fn(baseline_events)
            checks = evaluate_events(contract, mutant)
            target = checks[idx] if idx < len(checks) else None
            # Caught = the targeted check stopped passing (red OR inconclusive).
            if target is not None and target.status != "pass":
                killed += 1
            else:
                survivors.append(SurvivingMutant(
                    expectation_label=exp.label or exp.kind.value,
                    expectation_kind=exp.kind.value,
                    mutator=mut.name,
                    detail=mut.detail,
                ))

    # Trivial global mutant: a contract that passes on no data at all is pure theater.
    total += 1
    if overall_status(evaluate_events(contract, [])) != "pass":
        killed += 1
    else:
        survivors.append(SurvivingMutant(
            expectation_label="<contract>", expectation_kind="<all>",
            mutator="empty_stream", detail="contract still passed on an empty event stream",
        ))

    return MutationReport(
        contract_id=contract.id, baseline_passed=True,
        total_mutants=total, killed=killed, survived=len(survivors),
        score=round(killed / total, 3) if total else 0.0,
        meaningful=(len(survivors) == 0),
        survivors=survivors, note=note,
    )


def assess_minimality(
    contract: VerificationContract, baseline_events: list[RuntimeEvent]
) -> MinimalityReport:
    """Leave-one-out: is every expectation load-bearing? (the dual of break-on-purpose).

    For each expectation, apply the mutation(s) that target it; if the FULL contract
    catches the break but the contract WITHOUT that expectation does NOT, the
    expectation is the *unique* catcher (load-bearing). An expectation that uniquely
    catches nothing is redundant — the contract would catch the same breaks without it
    (overlapping checks) — or a coverage gap. Pairs/combos add nothing here: a combined
    mutation is caught whenever each single one is, so single mutations suffice.
    """
    if overall_status(evaluate_events(contract, baseline_events)) != "pass":
        return MinimalityReport(
            contract_id=contract.id, baseline_passed=False, minimal=False,
            note="baseline stream did not PASS; provide a passing stream before assessing.",
        )

    exps = contract.expectations
    coverage = [
        ExpectationCoverage(index=i, kind=e.kind.value, label=e.label or e.kind.value,
                            unique_kills=0, load_bearing=False)
        for i, e in enumerate(exps)
    ]
    for i, e in enumerate(exps):
        if e.kind not in _ASSESSABLE:
            continue
        reduced = contract.model_copy(
            update={"expectations": [x for j, x in enumerate(exps) if j != i]}
        )
        for mut in _mutators_for(e):
            mutant = mut.fn(baseline_events)
            caught_full = overall_status(evaluate_events(contract, mutant)) != "pass"
            if not caught_full:
                continue
            # No other expectation catches it iff the reduced contract now PASSES.
            caught_reduced = overall_status(evaluate_events(reduced, mutant)) != "pass"
            if not caught_reduced:
                coverage[i].unique_kills += 1

    for c in coverage:
        c.load_bearing = c.unique_kills > 0
    redundant = [c.index for c in coverage if not c.load_bearing]
    note = ""
    if redundant:
        labels = ", ".join(f"#{c.index} {c.label}" for c in coverage if c.index in redundant)
        note = f"redundant/uncovered expectations (no unique kill): {labels}"
    return MinimalityReport(
        contract_id=contract.id, baseline_passed=True, expectations=coverage,
        redundant=redundant, minimal=(not redundant), note=note,
    )
