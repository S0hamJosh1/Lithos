"""Nordic nRF52 family adapter.

Real implementation for the MVP demo path: Seeed XIAO nRF52840 Sense, plus
nRF52840-DK. Uses `west` for build/flash (Zephyr / nRF Connect SDK).

This is the moat path — Soham's research-lab board.
"""
from __future__ import annotations

import re
import shutil
import time
import uuid
from pathlib import Path

from ..models import (
    BoardFamily,
    BoardProfile,
    BuildResult,
    DetectedBoard,
    Diagnostic,
    FlashResult,
    OutputConfig,
    OutputSession,
    ProjectConfig,
    ProjectMetadata,
    ProjectResult,
    ReceiverKind,
    RepairHint,
    VerificationContract,
    VerificationResult,
    now_ms,
)
from ..runners import run_subprocess
from .base import BoardAdapter


# Static board profiles. Per PDF Section 13.1.
NRF52_PROFILES: dict[str, BoardProfile] = {
    "seeed_xiao_nrf52840_sense": BoardProfile(
        id="seeed_xiao_nrf52840_sense",
        family=BoardFamily.NRF52,
        display_name="Seeed XIAO nRF52840 Sense",
        frameworks=["zephyr", "arduino"],
        default_framework="zephyr",
        build_tool="west build",
        flash_tool="west flash",
        default_serial_baud=115200,
        capabilities=["ble", "imu", "i2c", "spi", "uart", "gpio", "pdm"],
        verification_receivers=[ReceiverKind.SERIAL, ReceiverKind.BLE],
        # Seeed XIAO uses MBED USB CDC during DAPLink mode; VID 0x2886 product 0x0045
        usb_vid_pids=[(0x2886, 0x0045), (0x239A, 0x80F4)],
    ),
    "nrf52840_dk": BoardProfile(
        id="nrf52840_dk",
        family=BoardFamily.NRF52,
        display_name="Nordic nRF52840 DK",
        frameworks=["zephyr", "ncs"],
        default_framework="zephyr",
        build_tool="west build",
        flash_tool="west flash",
        default_serial_baud=115200,
        capabilities=["ble", "i2c", "spi", "uart", "gpio", "usb"],
        verification_receivers=[ReceiverKind.SERIAL, ReceiverKind.BLE],
        # SEGGER J-Link OB
        usb_vid_pids=[(0x1366, 0x1015), (0x1366, 0x1051)],
    ),
}


class NRF52Adapter(BoardAdapter):
    """Real adapter — invokes `west` and pyserial."""

    id = "nrf52-adapter"
    family = BoardFamily.NRF52
    profiles = NRF52_PROFILES

    # ----- Detection -----

    async def detect(self) -> list[DetectedBoard]:
        """USB-serial enumeration + VID/PID match against our profiles.

        Confidence rises when (a) VID/PID matches AND (b) the serial device
        is currently enumerated. Falls back to manufacturer string + product
        string fuzzy match for boards we don't know yet.
        """
        try:
            from serial.tools import list_ports
        except ImportError:
            return []

        detected: list[DetectedBoard] = []
        for port in list_ports.comports():
            vid_pid = (port.vid or 0, port.pid or 0)
            profile_id: str | None = None
            confidence = 0.0

            # Strong match: VID/PID is in a known profile.
            for prof in NRF52_PROFILES.values():
                if vid_pid in prof.usb_vid_pids:
                    profile_id = prof.id
                    confidence = 0.95
                    break

            # Weak match: manufacturer / description string mentions nRF or XIAO.
            if profile_id is None:
                desc = " ".join(filter(None, [port.manufacturer, port.product, port.description])).lower()
                if "xiao" in desc and "nrf52840" in desc:
                    profile_id = "seeed_xiao_nrf52840_sense"
                    confidence = 0.65
                elif "nrf52840" in desc or "j-link" in desc.lower():
                    profile_id = "nrf52840_dk"
                    confidence = 0.55

            if profile_id is None:
                continue

            prof = NRF52_PROFILES[profile_id]
            detected.append(
                DetectedBoard(
                    id=f"{profile_id}-{port.device}",
                    profile_id=profile_id,
                    family=BoardFamily.NRF52,
                    serial_port=port.device,
                    serial_baud_default=prof.default_serial_baud,
                    usb_vid_pid=vid_pid if vid_pid != (0, 0) else None,
                    capabilities=prof.capabilities,
                    confidence=confidence,
                )
            )
        return detected

    # ----- Project lifecycle -----

    async def create_project(self, config: ProjectConfig) -> ProjectResult:
        """Scaffold a Zephyr project skeleton. Minimal viable.

        Caller is expected to drop their own main.c / sensor code into src/.
        We generate prj.conf + CMakeLists.txt + a sample main.c that boots the
        UART and prints BOOT_OK — which is the smallest verifiable behavior.
        """
        if config.profile_id not in NRF52_PROFILES:
            return ProjectResult(success=False, root=config.target_dir,
                                 error=f"unknown profile_id {config.profile_id!r}")

        root = Path(config.target_dir).expanduser().resolve()
        root.mkdir(parents=True, exist_ok=True)
        (root / "src").mkdir(exist_ok=True)

        # west uses underscored Zephyr board names (e.g. XIAO is `xiao_ble_sense`).
        zephyr_board = {
            "seeed_xiao_nrf52840_sense": "xiao_ble_sense",
            "nrf52840_dk": "nrf52840dk_nrf52840",
        }[config.profile_id]

        (root / "CMakeLists.txt").write_text(
            'cmake_minimum_required(VERSION 3.20.0)\n'
            f'set(BOARD {zephyr_board})\n'
            'find_package(Zephyr REQUIRED HINTS $ENV{ZEPHYR_BASE})\n'
            f'project({config.name})\n'
            'target_sources(app PRIVATE src/main.c)\n',
            encoding="utf-8",
        )
        (root / "prj.conf").write_text(
            "CONFIG_PRINTK=y\n"
            "CONFIG_LOG=y\n"
            "CONFIG_SERIAL=y\n"
            "CONFIG_UART_CONSOLE=y\n",
            encoding="utf-8",
        )
        (root / "src" / "main.c").write_text(
            '#include <zephyr/kernel.h>\n'
            '#include <zephyr/sys/printk.h>\n\n'
            'int main(void) {\n'
            '    printk("BOOT_OK\\n");\n'
            '    while (1) {\n'
            '        printk("HEARTBEAT %lld\\n", k_uptime_get());\n'
            '        k_msleep(1000);\n'
            '    }\n'
            '    return 0;\n'
            '}\n',
            encoding="utf-8",
        )

        meta = ProjectMetadata(
            name=config.name,
            profile_id=config.profile_id,
            framework=config.framework,
            root=str(root),
            detected_files=["CMakeLists.txt", "prj.conf", "src/main.c"],
        )
        return ProjectResult(success=True, root=str(root), metadata=meta)

    async def import_project(self, path: str) -> ProjectMetadata:
        root = Path(path).expanduser().resolve()
        files = []
        framework = "zephyr"
        profile_id = "seeed_xiao_nrf52840_sense"
        for name in ("CMakeLists.txt", "prj.conf", "west.yml", "platformio.ini", "arduino.json"):
            if (root / name).exists():
                files.append(name)
        if "platformio.ini" in files:
            framework = "platformio"
        elif "arduino.json" in files:
            framework = "arduino"
        return ProjectMetadata(
            name=root.name, profile_id=profile_id, framework=framework,
            root=str(root), detected_files=files,
        )

    # ----- Build / Flash -----

    async def build(self, project_path: str) -> BuildResult:
        west = shutil.which("west")
        if west is None:
            return BuildResult(
                success=False, command="west build", exit_code=127, duration_ms=0,
                summary="west is not installed; see https://docs.nordicsemi.com/bundle/ncs-latest/page/nrf/installation.html",
            )
        start = time.perf_counter()
        proc = await run_subprocess([west, "build", "-p", "always"], cwd=project_path)
        duration_ms = int((time.perf_counter() - start) * 1000)

        diagnostics = _parse_gcc_diagnostics(proc.stderr or proc.stdout)
        artifacts: list[str] = []
        build_dir = Path(project_path) / "build" / "zephyr"
        for art_name in ("zephyr.hex", "zephyr.bin", "zephyr.uf2", "zephyr.elf"):
            p = build_dir / art_name
            if p.exists():
                artifacts.append(str(p))

        return BuildResult(
            success=proc.returncode == 0,
            command=" ".join(proc.cmd),
            exit_code=proc.returncode,
            duration_ms=duration_ms,
            artifacts=artifacts,
            diagnostics=diagnostics,
            raw_log_path=proc.log_path,
            summary=f"{len(diagnostics)} diagnostics; {len(artifacts)} artifacts",
        )

    async def flash(self, project_path: str, device: DetectedBoard) -> FlashResult:
        west = shutil.which("west")
        if west is None:
            return FlashResult(
                success=False, command="west flash", exit_code=127, duration_ms=0,
                summary="west is not installed",
                failure_class="unknown",
            )
        start = time.perf_counter()
        cmd = [west, "flash"]
        # For UF2-bootloader XIAOs we'd point at the mass storage; for now west handles both.
        proc = await run_subprocess(cmd, cwd=project_path)
        duration_ms = int((time.perf_counter() - start) * 1000)

        failure_class = None
        if proc.returncode != 0:
            failure_class = _classify_flash_failure(proc.stderr or proc.stdout)

        return FlashResult(
            success=proc.returncode == 0,
            command=" ".join(proc.cmd),
            exit_code=proc.returncode,
            duration_ms=duration_ms,
            reconnected=False,           # caller decides via post-flash detect
            raw_log_path=proc.log_path,
            summary="ok" if proc.returncode == 0 else f"flash failed ({failure_class})",
            failure_class=failure_class,
        )

    # ----- Runtime / verification -----

    async def open_output_channel(
        self, device: DetectedBoard, config: OutputConfig
    ) -> OutputSession:
        """Create a receiver session. Caller must use evcide.receivers to actually pump events."""
        stream_id = f"strm-{uuid.uuid4().hex[:10]}"
        # Adapt config defaults from the detected board if not provided.
        if config.receiver == ReceiverKind.SERIAL:
            if not config.serial_port:
                config = config.model_copy(update={"serial_port": device.serial_port})
            if not config.serial_baud:
                config = config.model_copy(update={"serial_baud": device.serial_baud_default})
        return OutputSession(
            stream_id=stream_id,
            receiver=config.receiver,
            board_id=device.id,
            started_at_ms=now_ms(),
            config=config,
        )

    async def verify(
        self, session: OutputSession, contract: VerificationContract
    ) -> VerificationResult:
        # Verification orchestration lives in evcide.verify so a single engine handles
        # every adapter. We delegate here.
        from ..verify import run_verification

        return await run_verification(session, contract, adapter=self)

    async def repair_hints(self, result: VerificationResult) -> list[RepairHint]:
        # Repair loop v2: delegate to the shared, data-driven repair layer with this
        # adapter's framework so Zephyr-specific prj.conf settings are overlaid. The
        # per-adapter hint ladder lived here in v1; it is now one shared brain.
        from ..repair import build_repair_hints

        return build_repair_hints(result, framework="zephyr")


# ============ Diagnostic + failure parsers ============

_GCC_DIAG_RE = re.compile(
    r"^(?P<file>[^:]+):(?P<line>\d+):(?P<col>\d+):\s+"
    r"(?P<severity>error|warning|note):\s+(?P<message>.+?)\s*(?:\[(?P<code>[-\w]+)\])?$"
)


def _parse_gcc_diagnostics(text: str) -> list[Diagnostic]:
    out: list[Diagnostic] = []
    if not text:
        return out
    for line in text.splitlines():
        m = _GCC_DIAG_RE.match(line.strip())
        if not m:
            continue
        out.append(Diagnostic(
            file=m.group("file"),
            line=int(m.group("line")),
            column=int(m.group("col")),
            severity=m.group("severity"),
            message=m.group("message"),
            code=m.group("code"),
        ))
    return out


_FLASH_FAILURE_PATTERNS: list[tuple[str, str]] = [
    (r"no devices? found", "no_device"),
    (r"permission denied|access denied", "perm_denied"),
    (r"bootloader.*missing|not in dfu", "bootloader_missing"),
    (r"could not open port|no such device|device disconnected", "wrong_port"),
    (r"target locked|probe locked|debug access disabled", "probe_locked"),
    (r"timed? ?out|deadline exceeded", "timeout"),
]


def _classify_flash_failure(text: str) -> str:
    low = (text or "").lower()
    for pat, cls in _FLASH_FAILURE_PATTERNS:
        if re.search(pat, low):
            return cls
    return "unknown"
