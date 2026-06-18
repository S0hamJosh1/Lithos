"""Offline end-to-end demo of the verify → repair → assess loop.

No hardware, no LLM, no `west`. Drives the whole moat on synthetic event streams
so you can SEE it work on any dev box:

  python scripts/demo_loop.py

Flow:
  1. Author a contract from the terse DSL.
  2. A "broken" firmware project (prj.conf with the serial console disabled).
  3. Verify against a SILENT stream  -> FAIL (no_serial_data).
  4. Repair hints + the LLM-ready fix prompt.
  5. SettingsFixProvider proposes a config diff and applies it (no LLM).
  6. Re-verify against a HEALTHY stream -> PASS.
  7. Break-on-purpose: prove the now-passing contract is still meaningful.
"""
from __future__ import annotations

import asyncio
import sys
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))

from evcide.dsl import parse_contract                                        # noqa: E402
from evcide.models import ReceiverKind, RuntimeEvent                         # noqa: E402
from evcide.mutation import assess_contract                                  # noqa: E402
from evcide.repair import (                                                  # noqa: E402
    SettingsFixProvider, build_repair_hints, build_repair_request, run_repair_loop,
)
from evcide.verify import evaluate_events, overall_status                    # noqa: E402

CONTRACT_DSL = """
@id xiao-boot-imu
@target seeed_xiao_nrf52840_sense
@receiver serial
@timeout 12000

contains BOOT_OK within_ms:3000
no_timeout 1500
field accel_x not_frozen
"""


def _print(label: str) -> None:
    print(f"\n=== {label} ===")


def _ev(text: str, t_ms: int, parsed=None) -> RuntimeEvent:
    return RuntimeEvent(source=ReceiverKind.SERIAL, timestamp_ms=t_ms, board_id="xiao",
                        stream_id="demo", type="line", raw=text, parsed=parsed)


def _silent_stream() -> list[RuntimeEvent]:
    return []  # board never talks (serial console disabled)


def _healthy_stream() -> list[RuntimeEvent]:
    evs = [_ev("BOOT_OK ready", 100)]
    evs += [_ev("imu", 300 + 200 * i, parsed={"accel_x": float(i % 5) - 2.0}) for i in range(10)]
    return evs


def _verdict(checks) -> str:
    return overall_status(checks).upper()


async def main() -> int:
    _print("1. Author the contract (DSL)")
    print(CONTRACT_DSL.strip())
    contract = parse_contract(CONTRACT_DSL)
    print(f"\nparsed {len(contract.expectations)} expectations for {contract.target}")

    _print("2. The broken firmware (serial console disabled in prj.conf)")
    project = Path(tempfile.mkdtemp(prefix="evcide-demo-"))
    (project / "prj.conf").write_text("CONFIG_PRINTK=y\nCONFIG_LOG=y\n", encoding="utf-8")
    print(f"project: {project}")
    print((project / "prj.conf").read_text(encoding="utf-8").rstrip())

    _print("3. Verify against a SILENT stream (board not talking)")
    checks = evaluate_events(contract, _silent_stream())
    print(f"verdict: {_verdict(checks)}")
    for c in checks:
        print(f"  - {c.expectation.kind.value}: {c.status}")

    # Build a VerificationResult-shaped object for the repair layer.
    from evcide.models import RuntimeEvidence, VerificationResult, iso_now
    result = VerificationResult(
        status=overall_status(checks), contract_id=contract.id,
        started_at=iso_now(), ended_at=iso_now(), checks=checks,
        evidence=[RuntimeEvidence(stream_id="demo", receiver=ReceiverKind.SERIAL,
                                  event_count=0, duration_ms=0)],
        failure_classification="no_serial_data",
    )

    _print("4. Repair hints + LLM-ready fix prompt")
    result.repair_hints = build_repair_hints(result, framework="zephyr")
    request = build_repair_request(result, framework="zephyr")
    for h in result.repair_hints:
        print(f"  [{h.severity}] {h.classification}: {h.suggestion}")
        if h.target_settings:
            print(f"      settings: {h.target_settings}")
    print("\n--- prompt an LLM FixProvider would receive ---")
    print(request.prompt)

    _print("5. SettingsFixProvider - deterministic config fix (no LLM)")
    provider = SettingsFixProvider(project)
    proposal = await provider.propose_fix(request)
    print(proposal.explanation)
    print(proposal.diff)

    _print("6. Re-verify after the fix (healthy stream)")

    async def reverify(_proposal):
        provider.apply(request)  # write the config change
        return VerificationResult(
            status=overall_status(evaluate_events(contract, _healthy_stream())),
            contract_id=contract.id, started_at=iso_now(), ended_at=iso_now(),
            checks=evaluate_events(contract, _healthy_stream()), evidence=[],
        )

    best = await run_repair_loop(request, provider, reverify, n=1, assess=None)
    print(f"prj.conf now:\n{(project / 'prj.conf').read_text(encoding='utf-8').rstrip()}")
    print(f"\nrepair accepted: {best.accepted} - {best.reason}")
    print(f"re-verify verdict: {best.reverify_status.upper()}")

    _print("7. Break-on-purpose - is the now-passing contract meaningful?")
    report = assess_contract(contract, _healthy_stream())
    print(f"mutants: {report.total_mutants}  killed: {report.killed}  survived: {report.survived}")
    print(f"mutation score: {report.score}   meaningful: {report.meaningful}")
    if report.survivors:
        for s in report.survivors:
            print(f"  SURVIVOR (theater): {s.expectation_label} / {s.mutator}")

    _print("Done")
    print("verify -> repair -> fix -> re-verify -> break-on-purpose, all offline.")
    return 0


if __name__ == "__main__":
    raise SystemExit(asyncio.run(main()))
