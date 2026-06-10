"""End-to-end demo for the Seeed XIAO nRF52840 Sense path.

Usage:
  python scripts/demo_xiao.py detect
  python scripts/demo_xiao.py run --project ./your_project_dir \
                                  --contract ./examples/xiao_imu_ble_stream.json
  python scripts/demo_xiao.py scaffold --target /tmp/blink --profile seeed_xiao_nrf52840_sense

If `west` is not on PATH, the build/flash steps surface a clear error pointing
at the Nordic install docs; everything else still runs and reports.
"""
from __future__ import annotations

import argparse
import asyncio
import json
import sys
from pathlib import Path

# Allow `python scripts/demo_xiao.py ...` without an install
ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))

from evcide.adapters import detect_all_boards                                # noqa: E402
from evcide.adapters.registry import get_adapter_for_profile                 # noqa: E402
from evcide.models import (                                                  # noqa: E402
    OutputConfig, ProjectConfig, VerificationContract,
)


def _print_section(label: str) -> None:
    print(f"\n=== {label} ===")


async def cmd_detect() -> int:
    _print_section("Detecting boards")
    boards = await detect_all_boards()
    if not boards:
        print("  (no boards detected)")
        return 0
    for b in boards:
        print(f"  {b.id:48s} profile={b.profile_id:32s} "
              f"port={b.serial_port or '-':12s} confidence={b.confidence:.2f}")
    return 0


async def cmd_scaffold(target: str, profile: str, name: str) -> int:
    _print_section(f"Scaffolding {profile} -> {target}")
    adapter = get_adapter_for_profile(profile)
    result = await adapter.create_project(ProjectConfig(
        profile_id=profile, name=name, framework="zephyr", target_dir=target,
    ))
    if not result.success:
        print(f"  scaffold failed: {result.error}")
        return 1
    print(f"  scaffolded at {result.root}")
    if result.metadata:
        for f in result.metadata.detected_files:
            print(f"    {f}")
    return 0


async def cmd_run(project: str, contract_path: str) -> int:
    contract = VerificationContract.model_validate_json(
        Path(contract_path).read_text(encoding="utf-8")
    )
    profile = contract.target
    adapter = get_adapter_for_profile(profile)

    _print_section("Detecting target board")
    boards = await adapter.detect()
    if not boards:
        print("  no matching board connected. Aborting.")
        return 1
    device = boards[0]
    print(f"  using {device.id} (port={device.serial_port})")

    _print_section("Build")
    build = await adapter.build(project)
    print(f"  exit={build.exit_code} duration={build.duration_ms}ms "
          f"diagnostics={len(build.diagnostics)} artifacts={len(build.artifacts)}")
    if not build.success:
        print("  build failed - skipping flash + verify.")
        print(f"  raw log: {build.raw_log_path}")
        return 2

    _print_section("Flash")
    flash = await adapter.flash(project, device)
    print(f"  exit={flash.exit_code} duration={flash.duration_ms}ms class={flash.failure_class}")
    if not flash.success:
        print("  flash failed - skipping verify.")
        print(f"  raw log: {flash.raw_log_path}")
        return 3

    _print_section("Verify")
    rdef = contract.receivers[0]
    cfg = OutputConfig(
        receiver=rdef.type,
        serial_port=(None if rdef.port == "auto" else rdef.port),
        serial_baud=rdef.baud,
        file_path=rdef.file_path,
    )
    session = await adapter.open_output_channel(device, cfg)
    result = await adapter.verify(session, contract)
    print(f"  status={result.status} class={result.failure_classification}")
    print(f"  {result.agent_summary}")
    for c in result.checks:
        ok = {"pass": "OK", "fail": "FAIL", "inconclusive": "INC"}[c.status]
        label = c.expectation.label or c.expectation.kind.value
        print(f"    [{ok:4s}] {label}  actual={c.actual}  {c.message}")
    if result.repair_hints:
        print("  Repair hints:")
        for h in result.repair_hints:
            print(f"    - ({h.severity}) {h.classification}: {h.suggestion}")

    print("\nFull result (JSON):")
    print(json.dumps(result.model_dump(), indent=2, default=str))
    return 0 if result.status == "pass" else 4


def main() -> None:
    parser = argparse.ArgumentParser(prog="demo_xiao")
    sub = parser.add_subparsers(dest="cmd", required=True)
    sub.add_parser("detect")

    sc = sub.add_parser("scaffold")
    sc.add_argument("--target", required=True)
    sc.add_argument("--profile", default="seeed_xiao_nrf52840_sense")
    sc.add_argument("--name", default="blink")

    r = sub.add_parser("run")
    r.add_argument("--project", required=True)
    r.add_argument("--contract", required=True)

    args = parser.parse_args()
    if args.cmd == "detect":
        rc = asyncio.run(cmd_detect())
    elif args.cmd == "scaffold":
        rc = asyncio.run(cmd_scaffold(args.target, args.profile, args.name))
    elif args.cmd == "run":
        rc = asyncio.run(cmd_run(args.project, args.contract))
    else:
        parser.error(f"unknown command {args.cmd!r}")
        rc = 1
    sys.exit(rc)


if __name__ == "__main__":
    main()
