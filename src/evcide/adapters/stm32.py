"""STM32 family adapter — STUB.

Real implementation should wrap arm-none-eabi-gcc + CMake/ninja + OpenOCD
or STM32CubeProgrammer. Profiles: Nucleo-F401RE / F446RE / H743ZI.
Not implemented yet; raises NotImplementedError per dead-deps-theater rule.
"""
from __future__ import annotations

from ..models import (
    BoardFamily, BoardProfile, BuildResult, DetectedBoard, FlashResult,
    OutputConfig, OutputSession, ProjectConfig, ProjectMetadata, ProjectResult,
    ReceiverKind, RepairHint, VerificationContract, VerificationResult,
)
from .base import BoardAdapter


STM32_PROFILES: dict[str, BoardProfile] = {
    "nucleo_f446re": BoardProfile(
        id="nucleo_f446re", family=BoardFamily.STM32,
        display_name="STM32 Nucleo-F446RE",
        frameworks=["cube_hal", "cmake_stm32", "platformio"],
        default_framework="cube_hal",
        build_tool="cmake/make/ninja or CubeIDE headless",
        flash_tool="OpenOCD / ST-Link / STM32CubeProgrammer",
        capabilities=["uart", "i2c", "spi", "can", "timers", "dma", "adc"],
        verification_receivers=[ReceiverKind.SERIAL],
        # ST-Link/V2.1, V3
        usb_vid_pids=[(0x0483, 0x374B), (0x0483, 0x3754)],
    ),
}


class STM32Adapter(BoardAdapter):
    id = "stm32-adapter"
    family = BoardFamily.STM32
    profiles = STM32_PROFILES

    async def detect(self) -> list[DetectedBoard]:
        # Minimal: enumerate ST-Link VID/PIDs. Real impl: + OpenOCD target info,
        # ST-Link probe enumeration via libusb.
        try:
            from serial.tools import list_ports
        except ImportError:
            return []
        out = []
        for port in list_ports.comports():
            if (port.vid, port.pid) in STM32_PROFILES["nucleo_f446re"].usb_vid_pids:
                out.append(DetectedBoard(
                    id=f"stm32-{port.device}", profile_id="nucleo_f446re",
                    family=BoardFamily.STM32, serial_port=port.device,
                    serial_baud_default=115200, confidence=0.7,
                    capabilities=STM32_PROFILES["nucleo_f446re"].capabilities,
                ))
        return out

    async def create_project(self, config: ProjectConfig) -> ProjectResult:
        raise NotImplementedError("STM32 adapter scaffolding not implemented yet; "
                                  "use the nRF52 adapter for the MVP demo path")

    async def import_project(self, path: str) -> ProjectMetadata:
        raise NotImplementedError("STM32 adapter import not implemented yet")

    async def build(self, project_path: str) -> BuildResult:
        raise NotImplementedError("STM32 build runner not implemented yet")

    async def flash(self, project_path: str, device: DetectedBoard) -> FlashResult:
        raise NotImplementedError("STM32 flash runner not implemented yet")

    async def open_output_channel(
        self, device: DetectedBoard, config: OutputConfig
    ) -> OutputSession:
        raise NotImplementedError("STM32 output channel not implemented yet")

    async def verify(
        self, session: OutputSession, contract: VerificationContract
    ) -> VerificationResult:
        raise NotImplementedError("STM32 verification not implemented yet")

    async def repair_hints(self, result: VerificationResult) -> list[RepairHint]:
        return []
