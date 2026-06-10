"""ESP32 family adapter — STUB.

Real implementation should wrap idf.py + esptool.py. Profiles cover
ESP32 DevKitC, ESP32-S3 DevKitC, ESP32-C3 / C6 boards. Not implemented yet.
"""
from __future__ import annotations

from ..models import (
    BoardFamily, BoardProfile, BuildResult, DetectedBoard, FlashResult,
    OutputConfig, OutputSession, ProjectConfig, ProjectMetadata, ProjectResult,
    ReceiverKind, RepairHint, VerificationContract, VerificationResult,
)
from .base import BoardAdapter


ESP32_PROFILES: dict[str, BoardProfile] = {
    "esp32_devkitc": BoardProfile(
        id="esp32_devkitc", family=BoardFamily.ESP32,
        display_name="ESP32 DevKitC",
        frameworks=["esp-idf", "arduino"],
        default_framework="esp-idf",
        build_tool="idf.py build",
        flash_tool="idf.py flash",
        capabilities=["wifi", "ble", "uart", "i2c", "spi", "adc"],
        verification_receivers=[ReceiverKind.SERIAL, ReceiverKind.SOCKET, ReceiverKind.BLE],
        usb_vid_pids=[(0x10C4, 0xEA60), (0x1A86, 0x7523)],  # CP210x, CH340
    ),
    "esp32s3_devkitc": BoardProfile(
        id="esp32s3_devkitc", family=BoardFamily.ESP32,
        display_name="ESP32-S3 DevKitC",
        frameworks=["esp-idf", "arduino"],
        default_framework="esp-idf",
        build_tool="idf.py build",
        flash_tool="idf.py flash",
        capabilities=["wifi", "ble", "uart", "i2c", "spi", "usb-otg"],
        verification_receivers=[ReceiverKind.SERIAL, ReceiverKind.SOCKET, ReceiverKind.BLE],
        usb_vid_pids=[(0x303A, 0x1001)],
    ),
}


class ESP32Adapter(BoardAdapter):
    id = "esp32-adapter"
    family = BoardFamily.ESP32
    profiles = ESP32_PROFILES

    async def detect(self) -> list[DetectedBoard]:
        try:
            from serial.tools import list_ports
        except ImportError:
            return []
        out = []
        for port in list_ports.comports():
            for prof in ESP32_PROFILES.values():
                if (port.vid, port.pid) in prof.usb_vid_pids:
                    out.append(DetectedBoard(
                        id=f"{prof.id}-{port.device}", profile_id=prof.id,
                        family=BoardFamily.ESP32, serial_port=port.device,
                        serial_baud_default=115200, confidence=0.85,
                        capabilities=prof.capabilities,
                    ))
                    break
        return out

    async def create_project(self, config: ProjectConfig) -> ProjectResult:
        raise NotImplementedError("ESP32 adapter not implemented yet; "
                                  "use the nRF52 adapter for the MVP demo path")

    async def import_project(self, path: str) -> ProjectMetadata:
        raise NotImplementedError("ESP32 import not implemented yet")

    async def build(self, project_path: str) -> BuildResult:
        raise NotImplementedError("ESP32 build runner not implemented yet")

    async def flash(self, project_path: str, device: DetectedBoard) -> FlashResult:
        raise NotImplementedError("ESP32 flash runner not implemented yet")

    async def open_output_channel(
        self, device: DetectedBoard, config: OutputConfig
    ) -> OutputSession:
        raise NotImplementedError("ESP32 output channel not implemented yet")

    async def verify(
        self, session: OutputSession, contract: VerificationContract
    ) -> VerificationResult:
        raise NotImplementedError("ESP32 verification not implemented yet")

    async def repair_hints(self, result: VerificationResult) -> list[RepairHint]:
        return []
