"""RP2040 / RP2350 family adapter — STUB.

Real implementation should wrap Pico SDK CMake + UF2 mass-storage flash.
Strategic role per spec: also a low-cost verification coprocessor / HIL tester.
"""
from __future__ import annotations

from ..models import (
    BoardFamily, BoardProfile, BuildResult, DetectedBoard, FlashResult,
    OutputConfig, OutputSession, ProjectConfig, ProjectMetadata, ProjectResult,
    ReceiverKind, RepairHint, VerificationContract, VerificationResult,
)
from .base import BoardAdapter


RP2040_PROFILES: dict[str, BoardProfile] = {
    "rp_pico": BoardProfile(
        id="rp_pico", family=BoardFamily.RP2040,
        display_name="Raspberry Pi Pico",
        frameworks=["pico_sdk", "micropython", "arduino"],
        default_framework="pico_sdk",
        build_tool="cmake/ninja (Pico SDK)",
        flash_tool="UF2 mass storage (or picotool)",
        capabilities=["uart", "i2c", "spi", "pio", "pwm", "adc"],
        verification_receivers=[ReceiverKind.SERIAL],
        usb_vid_pids=[(0x2E8A, 0x000A), (0x2E8A, 0x0003)],  # RP2 USB CDC / bootsel
    ),
}


class RP2040Adapter(BoardAdapter):
    id = "rp2040-adapter"
    family = BoardFamily.RP2040
    profiles = RP2040_PROFILES

    async def detect(self) -> list[DetectedBoard]:
        try:
            from serial.tools import list_ports
        except ImportError:
            return []
        out = []
        for port in list_ports.comports():
            if (port.vid, port.pid) in RP2040_PROFILES["rp_pico"].usb_vid_pids:
                out.append(DetectedBoard(
                    id=f"rp-{port.device}", profile_id="rp_pico",
                    family=BoardFamily.RP2040, serial_port=port.device,
                    serial_baud_default=115200, confidence=0.85,
                    capabilities=RP2040_PROFILES["rp_pico"].capabilities,
                ))
        return out

    async def create_project(self, config: ProjectConfig) -> ProjectResult:
        raise NotImplementedError("RP2040 adapter not implemented yet")

    async def import_project(self, path: str) -> ProjectMetadata:
        raise NotImplementedError("RP2040 import not implemented yet")

    async def build(self, project_path: str) -> BuildResult:
        raise NotImplementedError("RP2040 build runner not implemented yet")

    async def flash(self, project_path: str, device: DetectedBoard) -> FlashResult:
        raise NotImplementedError("RP2040 flash runner not implemented yet")

    async def open_output_channel(
        self, device: DetectedBoard, config: OutputConfig
    ) -> OutputSession:
        raise NotImplementedError("RP2040 output channel not implemented yet")

    async def verify(
        self, session: OutputSession, contract: VerificationContract
    ) -> VerificationResult:
        raise NotImplementedError("RP2040 verification not implemented yet")

    async def repair_hints(self, result: VerificationResult) -> list[RepairHint]:
        return []
