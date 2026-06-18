"""Repair loop v2 — PDF Section 23.

Turns a *failed* VerificationResult into (a) targeted, per-check RepairHints and
(b) a deterministic, LLM-ready RepairRequest (the "targeted fix prompt"). This is
the classifier→prompt half of the autonomous build→verify→repair loop.

Two design rules make it the moat layer rather than per-adapter boilerplate:

1. **Shared + data-driven.** One knowledge base (`REPAIR_KB`) keyed by a
   *per-check* classification, so every board family gets repair guidance for
   free instead of re-implementing a hint ladder. Board/framework specifics are
   a thin overlay (`FRAMEWORK_SETTINGS`), not a copy of the whole table.
2. **The model call is the only boundary.** Everything here — classification,
   hint synthesis, prompt rendering — is pure and runs with no hardware and no
   LLM, so it is fully testable on this box. The actual fix generation is an
   injected `FixProvider`; the default `NullFixProvider` refuses honestly rather
   than faking a patch.
"""
from __future__ import annotations

import difflib
import inspect
import re
from pathlib import Path
from typing import Protocol, runtime_checkable

from .models import (
    CheckResult,
    ExpectationKind,
    FixProposal,
    RepairAttempt,
    RepairHint,
    RepairRequest,
    RuntimeEvent,
    VerificationResult,
)


# ============ Per-check classification ============

# Whole-stream causes (no data at all) — these win over any per-check shape
# because the fix is "make the board talk", not "fix this one expectation".
_NO_DATA_CLASSES = {"no_serial_data", "no_runtime_data", "ble_not_advertising"}


def classify_check(check: CheckResult) -> str | None:
    """Map a single failed/inconclusive check to a repair classification.

    Returns None for a passing check. The expectation is embedded in the
    CheckResult, so this needs no contract — it generalizes verify._classify_failure
    from one whole-result verdict to one cause *per failing expectation*.
    """
    if check.status not in ("fail", "inconclusive"):
        return None
    k = check.expectation.kind
    msg = (check.message or "").lower()

    if k == ExpectationKind.CONTAINS:
        pat = (check.expectation.pattern or "").upper()
        return "boot_msg_missing" if "BOOT" in pat else "expected_token_missing"
    if k == ExpectationKind.NOT_CONTAINS:
        return "forbidden_token_present"
    if k in (ExpectationKind.COUNT_MIN, ExpectationKind.COUNT_MAX, ExpectationKind.COUNT_EXACT):
        return "count_unmet"
    if k == ExpectationKind.SEQUENCE:
        return "sequence_incomplete"
    if k in (ExpectationKind.MESSAGE_RATE_HZ, ExpectationKind.ROS_TOPIC_RATE_HZ):
        if "below" in msg:
            return "rate_too_low"
        if "above" in msg:
            return "rate_too_high"
        return "rate_out_of_band"
    if k == ExpectationKind.FIELD_PRESENT:
        return "field_missing"
    if k == ExpectationKind.FIELD_RANGE:
        return "field_out_of_range"
    if k == ExpectationKind.FIELD_NOT_FROZEN:
        return "field_frozen"
    if k == ExpectationKind.NO_NAN:
        return "field_nan"
    if k == ExpectationKind.NO_TIMEOUT:
        return "runtime_timeout"
    if k == ExpectationKind.BLE_ADVERTISING:
        return "ble_not_advertising"
    if k == ExpectationKind.GATT_SERVICE_PRESENT:
        return "gatt_service_missing"
    if k == ExpectationKind.SOCKET_REACHABLE:
        return "socket_unreachable"
    return "expectation_unmet"


# ============ Repair knowledge base ============

# classification -> (severity, suggestion, generic target files).
# severity: "block" (must fix) > "warn" (likely wrong) > "info" (inspect).
_BLOCK, _WARN, _INFO = "block", "warn", "info"

REPAIR_KB: dict[str, tuple[str, str, list[str]]] = {
    "no_serial_data": (
        _BLOCK,
        "No bytes received on serial. Confirm the UART/USB console is enabled and "
        "that the boot print targets it (not a disabled backend).",
        ["src/main.c", "prj.conf"],
    ),
    "no_runtime_data": (
        _BLOCK,
        "No runtime events arrived on any receiver inside the window. The board may "
        "not be running, not be connected, or printing to the wrong channel.",
        ["src/main.c"],
    ),
    "ble_not_advertising": (
        _BLOCK,
        "BLE advertisement not seen. Verify the Bluetooth stack is enabled and that "
        "advertising is started at boot.",
        ["src/main.c"],
    ),
    "boot_msg_missing": (
        _BLOCK,
        "Expected boot token not seen. Print the boot marker in main() before any "
        "blocking call, and check reset hooks aren't suppressing early output.",
        ["src/main.c"],
    ),
    "expected_token_missing": (
        _BLOCK,
        "A required output token never appeared. Confirm the code path that prints it "
        "actually runs and isn't gated behind an unmet condition or optimized out.",
        ["src/main.c"],
    ),
    "forbidden_token_present": (
        _BLOCK,
        "A forbidden token appeared (e.g. an error/fault marker). Trace the branch that "
        "emits it and remove the failing condition.",
        ["src/main.c"],
    ),
    "count_unmet": (
        _WARN,
        "Observed occurrence count is outside the contracted bound. Check the loop "
        "cadence and any early-exit that suppresses emissions.",
        ["src/main.c"],
    ),
    "sequence_incomplete": (
        _BLOCK,
        "The required event sequence did not complete in order. Verify each stage runs "
        "and emits its marker before the next begins.",
        ["src/main.c"],
    ),
    "rate_too_low": (
        _WARN,
        "Sample rate below the contracted minimum. Inspect sleep/print overhead on the "
        "hot path; raise loop priority or move logging off it.",
        ["src/main.c"],
    ),
    "rate_too_high": (
        _WARN,
        "Sample rate above the contracted maximum. Add a delay or downsample before "
        "emitting.",
        ["src/main.c"],
    ),
    "rate_out_of_band": (
        _WARN,
        "Sample rate outside the contracted band. Tune the loop period toward the "
        "expected rate.",
        ["src/main.c"],
    ),
    "field_missing": (
        _BLOCK,
        "A required parsed field is absent from the output. Ensure the field is "
        "serialized into every frame.",
        ["src/main.c"],
    ),
    "field_out_of_range": (
        _BLOCK,
        "A field value left its valid range. Check sensor scaling/units and fix the "
        "conversion or clamp the output.",
        ["src/main.c"],
    ),
    "field_frozen": (
        _BLOCK,
        "A field is frozen across consecutive samples — the value is stale. Ensure a "
        "fresh read happens every loop (e.g. sensor_sample_fetch before channel_get).",
        ["src/main.c"],
    ),
    # Alias for the legacy top-level classification.
    "imu_frozen": (
        _BLOCK,
        "IMU values are frozen across samples — likely a cached frame. Ensure "
        "sensor_sample_fetch() runs every loop before reading channels.",
        ["src/main.c"],
    ),
    "field_nan": (
        _BLOCK,
        "A field produced NaN. Guard divisions and uninitialized reads; validate sensor "
        "data before publishing.",
        ["src/main.c"],
    ),
    "runtime_timeout": (
        _BLOCK,
        "A no-timeout expectation tripped — the device stopped producing inside the "
        "window. Check for a hang/deadlock or a watchdog reset.",
        ["src/main.c"],
    ),
    "gatt_service_missing": (
        _BLOCK,
        "The expected GATT service was not exposed. Register the service/characteristic "
        "UUID in the BLE init.",
        ["src/main.c"],
    ),
    "socket_unreachable": (
        _BLOCK,
        "The verification socket was unreachable. Confirm the device joined the network "
        "and is listening on the contracted host:port.",
        ["src/main.c"],
    ),
    "wrong_baud": (
        _WARN,
        "Receiver baud differs from the board default. Align the contract receiver.baud "
        "with the firmware UART config.",
        [],
    ),
    "expectation_unmet": (
        _INFO,
        "Verification failed without a specifically classified cause. Inspect the raw "
        "evidence and tighten the contract.",
        [],
    ),
}

# Framework-specific settings overlay. Only entries that genuinely differ per
# framework live here; the generic suggestion above always applies. This is what
# replaces each adapter hand-rolling its own hint ladder.
FRAMEWORK_SETTINGS: dict[str, dict[str, dict[str, object]]] = {
    "zephyr": {
        "no_serial_data": {"prj.conf": {"CONFIG_UART_CONSOLE": "y", "CONFIG_USB_DEVICE_STACK": "y"}},
        "ble_not_advertising": {"prj.conf": {"CONFIG_BT": "y", "CONFIG_BT_PERIPHERAL": "y"}},
        "gatt_service_missing": {"prj.conf": {"CONFIG_BT": "y", "CONFIG_BT_GATT_DYNAMIC_DB": "y"}},
    },
}

_SEVERITY_ORDER = {_BLOCK: 0, _WARN: 1, _INFO: 2}


# ============ Hint synthesis ============

def _ordered_classifications(result: VerificationResult) -> list[str]:
    """Distinct repair classifications for a failed result, in discovery order.

    A whole-stream no-data cause short-circuits to a single class (fixing the
    one expectation is pointless if nothing is being received). Otherwise we
    collect one class per failing check, then fall back to the legacy top-level
    classification if no check produced one.
    """
    top = result.failure_classification
    if top in _NO_DATA_CLASSES:
        return [top]

    classes: list[str] = []
    for c in result.checks:
        cls = classify_check(c)
        if cls:
            classes.append(cls)
    if not classes and top:
        classes.append(top)

    seen: set[str] = set()
    return [c for c in classes if not (c in seen or seen.add(c))]


def build_repair_hints(result: VerificationResult, framework: str | None = None) -> list[RepairHint]:
    """Synthesize per-cause RepairHints from the shared KB, severity-ordered.

    Replaces the per-adapter hint ladders: any adapter that delegates here gets
    the full, consistent hint set; new adapters get repair guidance for free.
    """
    if result.status == "pass":
        return []

    overlay = FRAMEWORK_SETTINGS.get(framework or "", {})
    hints: list[RepairHint] = []
    for cls in _ordered_classifications(result):
        severity, suggestion, files = REPAIR_KB.get(cls, REPAIR_KB["expectation_unmet"])
        hints.append(
            RepairHint(
                classification=cls,
                severity=severity,
                suggestion=suggestion,
                target_files=list(files),
                target_settings=dict(overlay.get(cls, {})),
            )
        )
    hints.sort(key=lambda h: _SEVERITY_ORDER.get(h.severity, 99))
    return hints


# ============ Evidence digest + prompt rendering ============

def _ev_text(ev: RuntimeEvent) -> str:
    if isinstance(ev.raw, str):
        return ev.raw
    if isinstance(ev.raw, (bytes, bytearray)):
        return bytes(ev.raw).decode("utf-8", errors="replace")
    return ev.type


def _short(text: str, limit: int = 80) -> str:
    text = text.replace("\n", "\\n").strip()
    return text if len(text) <= limit else text[: limit - 1] + "…"


def _evidence_digest(result: VerificationResult) -> str:
    parts: list[str] = []
    for ev in result.evidence:
        line = f"{ev.receiver.value} stream: {ev.event_count} events over {ev.duration_ms}ms"
        samples = [_short(_ev_text(e)) for e in ev.sample_events[:3]]
        if samples:
            line += "; samples: " + " | ".join(samples)
        parts.append(line)
    return "\n".join(parts) if parts else "no events captured"


def _render_prompt(
    result: VerificationResult,
    hints: list[RepairHint],
    failed: list[CheckResult],
    evidence_digest: str,
) -> str:
    lines = [
        "You are repairing embedded firmware that failed runtime verification.",
        "",
        f"CONTRACT: {result.contract_id}",
        f"OVERALL: {result.status.upper()}",
        "",
        "FAILED CHECKS:",
    ]
    if failed:
        for c in failed:
            exp = c.expectation
            detail = exp.pattern or exp.field or exp.label or ""
            head = f"  - [{classify_check(c) or 'unmet'}] {exp.kind.value}"
            if detail:
                head += f' "{detail}"'
            lines.append(head + (f": {c.message}" if c.message else ""))
            if c.actual:
                lines.append(f"      observed: {c.actual}")
    else:
        lines.append("  (no single expectation failed; see classification + evidence)")

    lines += ["", "EVIDENCE:", *(f"  {ln}" for ln in evidence_digest.splitlines())]
    lines += ["", "LIKELY CAUSES & FIXES:"]
    for h in hints:
        lines.append(f"  - {h.classification} ({h.severity}): {h.suggestion}")
        if h.target_files:
            lines.append(f"      files: {', '.join(h.target_files)}")
        if h.target_settings:
            lines.append(f"      settings: {h.target_settings}")

    lines += [
        "",
        "Propose the minimal source change that makes every failed check pass. "
        "Do not weaken the contract. Output a unified diff against the project source.",
    ]
    return "\n".join(lines)


def build_repair_request(
    result: VerificationResult, framework: str | None = None
) -> RepairRequest:
    """Assemble the deterministic, LLM-ready repair prompt for a failed result.

    Reuses ``result.repair_hints`` if already populated (so an adapter's
    framework-specific settings survive); otherwise synthesizes them here.
    """
    hints = result.repair_hints or build_repair_hints(result, framework)
    failed = [c for c in result.checks if c.status == "fail"]
    if not failed:
        failed = [c for c in result.checks if c.status == "inconclusive"]

    target_files: list[str] = []
    for h in hints:
        for f in h.target_files:
            if f not in target_files:
                target_files.append(f)

    digest = _evidence_digest(result)
    return RepairRequest(
        contract_id=result.contract_id,
        overall_status=result.status,
        failure_classifications=[h.classification for h in hints],
        failed_checks=failed,
        hints=hints,
        target_files=target_files,
        evidence_digest=digest,
        prompt=_render_prompt(result, hints, failed, digest),
    )


# ============ Fix provider boundary (the only non-deterministic seam) ============

@runtime_checkable
class FixProvider(Protocol):
    """Consumes a RepairRequest and proposes a source change. The single point
    where an LLM (or a human, or a canned fixer in tests) enters the loop."""

    async def propose_fix(self, request: RepairRequest) -> FixProposal: ...


class NullFixProvider:
    """Default provider: no fixer wired. Refuses honestly so a silent no-op is
    never mistaken for a successful repair (the no-dead-deps-theater rule)."""

    async def propose_fix(self, request: RepairRequest) -> FixProposal:
        raise NotImplementedError(
            "No FixProvider configured. Wire an LLM-backed FixProvider to close the "
            "repair loop; the RepairRequest (request.prompt) is ready to send."
        )


_KCONFIG_KEY = re.compile(r"^\s*([A-Za-z0-9_]+)\s*=")


def _apply_kv(text: str, kv: dict[str, object]) -> list[str]:
    """Return the file's lines with `kv` set: replace an existing `KEY=...` line,
    append a new one otherwise. Mirrors how a Kconfig fragment is edited."""
    lines = text.splitlines()
    present: dict[str, int] = {}
    for i, ln in enumerate(lines):
        m = _KCONFIG_KEY.match(ln)
        if m:
            present[m.group(1)] = i
    out = list(lines)
    for key, val in kv.items():
        newline = f"{key}={val}"
        if key in present:
            out[present[key]] = newline
        else:
            out.append(newline)
    return out


def _settings_changes(root: Path, hints: list[RepairHint]) -> dict[str, tuple[str, list[str]]]:
    """{filename: (original_text, new_lines)} for every config file a hint wants to
    edit — but only where the edit actually changes something."""
    wanted: dict[str, dict[str, object]] = {}
    for h in hints:
        for fname, kv in (h.target_settings or {}).items():
            wanted.setdefault(fname, {}).update(kv)
    changes: dict[str, tuple[str, list[str]]] = {}
    for fname, kv in wanted.items():
        path = root / fname
        orig = path.read_text(encoding="utf-8") if path.exists() else ""
        new_lines = _apply_kv(orig, kv)
        if new_lines != orig.splitlines():
            changes[fname] = (orig, new_lines)
    return changes


class SettingsFixProvider:
    """The first *real* FixProvider — deterministic, no LLM, no hardware.

    Repair hints already carry framework `target_settings` (e.g. Zephyr
    `prj.conf` keys). Those fixes are mechanical: enable a Kconfig symbol. This
    provider turns them into an actual config diff and can apply it. It handles
    only the config-class of failures; a hint with no `target_settings`
    (source-level bug) is honestly left to an LLM provider — it never fakes a
    source fix.
    """

    def __init__(self, project_root: str | Path):
        self.root = Path(project_root)

    async def propose_fix(self, request: RepairRequest) -> FixProposal:
        changes = _settings_changes(self.root, request.hints)
        if not changes:
            return FixProposal(
                applied=False, diff=None, confidence=0.0,
                explanation="No mechanical config fix available; this failure needs a "
                            "source-level change — wire an LLM FixProvider (request.prompt is ready).",
            )
        diffs, files = [], []
        for fname, (orig, new_lines) in changes.items():
            diff = "\n".join(difflib.unified_diff(
                orig.splitlines(), new_lines,
                fromfile=f"a/{fname}", tofile=f"b/{fname}", lineterm="",
            ))
            diffs.append(diff)
            files.append(fname)
        return FixProposal(
            applied=False, diff="\n".join(diffs), confidence=0.6,
            explanation=f"Mechanical config fix for {', '.join(files)} from repair hints "
                        "(enables the Kconfig symbols the verification flagged as missing).",
        )

    def apply(self, request: RepairRequest) -> list[str]:
        """Write the config changes to disk. Returns the files changed."""
        changes = _settings_changes(self.root, request.hints)
        for fname, (_orig, new_lines) in changes.items():
            (self.root / fname).write_text("\n".join(new_lines) + "\n", encoding="utf-8")
        return list(changes.keys())


async def attempt_repair(
    result: VerificationResult,
    provider: FixProvider,
    framework: str | None = None,
) -> FixProposal:
    """Build the repair request (if needed) and ask the provider for a fix.

    The deterministic half runs here; the provider is the injected boundary.
    """
    request = result.repair_request or build_repair_request(result, framework)
    return await provider.propose_fix(request)


# ============ Closed repair loop (best-of-N + don't-game-the-metric) ============

async def _maybe_await(x):
    """Allow injected boundaries to be either sync or async."""
    return await x if inspect.isawaitable(x) else x


async def run_repair_loop(
    request: RepairRequest,
    provider: FixProvider,
    reverify,
    n: int = 3,
    assess=None,
) -> RepairAttempt | None:
    """Close the repair loop with the confidence-loop's two remaining traps.

    - **Keep N attempts alive (best-of-N).** Ask the provider for ``n`` candidate
      fixes; a single greedy attempt dead-ends in local optima (the doc's first trap).
    - **Never optimize the score (don't-game-the-metric).** A candidate is accepted
      only if it makes the contract PASS *and* the now-passing contract is still
      meaningful under break-on-purpose. A fix that passes by weakening the oracle
      is rejected (the doc's second trap), even though it's green.

    Boundaries are injected and may be sync or async:
      - ``provider.propose_fix(request) -> FixProposal`` (LLM)
      - ``reverify(proposal) -> VerificationResult`` (apply the fix, re-run on hardware)
      - ``assess(proposal) -> MutationReport`` (optional; re-check contract meaningfulness)

    Returns the best accepted RepairAttempt (highest proposal confidence). If none
    is accepted, returns the first rejected attempt (carrying its reason) so the
    caller learns *why*; returns None only when the provider produced no candidate.
    """
    candidates: list[FixProposal] = []
    for _ in range(max(1, n)):
        p = await _maybe_await(provider.propose_fix(request))
        if p is not None:
            candidates.append(p)

    attempts: list[RepairAttempt] = []
    for c in candidates:
        result = await _maybe_await(reverify(c))
        status = getattr(result, "status", "fail")
        if status != "pass":
            attempts.append(RepairAttempt(
                proposal=c, reverify_status=status, accepted=False,
                reason="fix did not make the contract pass",
            ))
            continue
        meaningful: bool | None = None
        if assess is not None:
            report = await _maybe_await(assess(c))
            meaningful = bool(getattr(report, "meaningful", False))
            if not meaningful:
                attempts.append(RepairAttempt(
                    proposal=c, reverify_status=status, contract_still_meaningful=False,
                    accepted=False,
                    reason="fix passes but degraded contract meaningfulness — gamed the metric",
                ))
                continue
        attempts.append(RepairAttempt(
            proposal=c, reverify_status=status, contract_still_meaningful=meaningful,
            accepted=True, reason="passing fix; contract still meaningful",
        ))

    accepted = [a for a in attempts if a.accepted]
    if accepted:
        return max(accepted, key=lambda a: a.proposal.confidence)
    return attempts[0] if attempts else None
