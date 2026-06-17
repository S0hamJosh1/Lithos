"""Project classifier — infer the board profile + toolchain from a project on disk.

Used by /projects/import when the caller does not pass an explicit profile_id.
Heuristics are evidence-based (marker files + parsed board names), ordered
strongest-first, and return None when nothing is determinable rather than
guessing — the API then asks the caller to specify a profile_id.

Pure filesystem inspection: no hardware, no network, fully unit-testable.
"""
from __future__ import annotations

import re
from pathlib import Path

from .models import BoardFamily, ProjectClassification

# Map a Zephyr/nRF-Connect `set(BOARD <x>)` name to one of our profile ids.
_ZEPHYR_BOARD_TO_PROFILE = {
    "xiao_ble_sense": "seeed_xiao_nrf52840_sense",
    "xiao_ble": "seeed_xiao_nrf52840_sense",
    "nrf52840dk_nrf52840": "nrf52840_dk",
}

_CMAKE_BOARD_RE = re.compile(r"set\(\s*BOARD\s+([A-Za-z0-9_]+)", re.IGNORECASE)
_CMAKE_DBOARD_RE = re.compile(r"-DBOARD=([A-Za-z0-9_]+)")
_INI_BOARD_RE = re.compile(r"^\s*board\s*=\s*(\S+)", re.IGNORECASE | re.MULTILINE)
_INI_PLATFORM_RE = re.compile(r"^\s*platform\s*=\s*(\S+)", re.IGNORECASE | re.MULTILINE)

_PROFILE_FAMILY = {
    "seeed_xiao_nrf52840_sense": BoardFamily.NRF52,
    "nrf52840_dk": BoardFamily.NRF52,
    "nucleo_f446re": BoardFamily.STM32,
    "esp32_devkitc": BoardFamily.ESP32,
    "esp32s3_devkitc": BoardFamily.ESP32,
    "rp_pico": BoardFamily.RP2040,
}


def _read(path: Path) -> str:
    try:
        return path.read_text(encoding="utf-8", errors="replace")
    except OSError:
        return ""


def _classification(profile_id: str, framework: str, confidence: float,
                    evidence: list[str]) -> ProjectClassification:
    return ProjectClassification(
        profile_id=profile_id, family=_PROFILE_FAMILY[profile_id],
        framework=framework, confidence=confidence, evidence=evidence,
    )


def _platformio_profile(board: str, platform: str) -> str | None:
    b, p = board.lower(), platform.lower()
    if "xiaoblesense" in b or "xiao_ble" in b or "nrf52840" in b:
        return "seeed_xiao_nrf52840_sense"
    if "esp32-s3" in b or "esp32s3" in b or "esp32dev_s3" in b:
        return "esp32s3_devkitc"
    if "esp32" in b or "espressif32" in p:
        return "esp32_devkitc"
    if "f446" in b or "nucleo_f446" in b:
        return "nucleo_f446re"
    if "pico" in b or "rp2040" in b:
        return "rp_pico"
    return None


def classify_project(path: str | Path) -> ProjectClassification | None:
    root = Path(path).expanduser()
    if not root.is_dir():
        return None

    def has(name: str) -> bool:
        return (root / name).exists()

    # 1) PlatformIO — board name is authoritative.
    if has("platformio.ini"):
        ini = _read(root / "platformio.ini")
        bm = _INI_BOARD_RE.search(ini)
        pm = _INI_PLATFORM_RE.search(ini)
        board = bm.group(1) if bm else ""
        platform = pm.group(1) if pm else ""
        profile = _platformio_profile(board, platform)
        if profile:
            return _classification(profile, "platformio", 0.9,
                                   [f"platformio.ini board={board or '?'} platform={platform or '?'}"])

    # 2) Zephyr / nRF Connect SDK — prj.conf + a BOARD in CMakeLists.
    if has("prj.conf"):
        cmake = _read(root / "CMakeLists.txt")
        m = _CMAKE_BOARD_RE.search(cmake) or _CMAKE_DBOARD_RE.search(cmake)
        if m:
            board = m.group(1)
            profile = _ZEPHYR_BOARD_TO_PROFILE.get(board.lower())
            if profile:
                return _classification(profile, "zephyr", 0.9,
                                       [f"prj.conf + CMakeLists set(BOARD {board})"])
        # prj.conf without a recognized board: assume the MVP nRF path, low confidence.
        return _classification("seeed_xiao_nrf52840_sense", "zephyr", 0.5,
                               ["prj.conf present; BOARD not recognized, defaulting to XIAO nRF52840"])

    # 3) ESP-IDF — sdkconfig markers.
    if has("sdkconfig") or has("sdkconfig.defaults"):
        text = _read(root / "sdkconfig") + _read(root / "sdkconfig.defaults")
        profile = "esp32s3_devkitc" if "esp32s3" in text.lower() else "esp32_devkitc"
        return _classification(profile, "esp-idf", 0.8, ["ESP-IDF sdkconfig present"])

    # 4) RP2040 Pico SDK.
    if has("pico_sdk_import.cmake"):
        return _classification("rp_pico", "pico-sdk", 0.85, ["pico_sdk_import.cmake present"])

    # 5) Arduino sketch — framework is clear, board is not determinable from a sketch.
    if any(root.glob("*.ino")):
        return None

    return None
