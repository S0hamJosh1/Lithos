"""Unit tests for the project classifier + the /projects/import wiring.

Pure filesystem fixtures via tmp_path — no hardware, no network.
"""
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))

from fastapi.testclient import TestClient

from evcide.api import create_app
from evcide.classify import classify_project


def _write(d: Path, name: str, text: str = "") -> None:
    (d / name).write_text(text, encoding="utf-8")


def test_zephyr_xiao_from_cmake_board(tmp_path):
    _write(tmp_path, "prj.conf", "CONFIG_PRINTK=y\n")
    _write(tmp_path, "CMakeLists.txt", "set(BOARD xiao_ble_sense)\nproject(x)\n")
    c = classify_project(tmp_path)
    assert c is not None
    assert c.profile_id == "seeed_xiao_nrf52840_sense"
    assert c.framework == "zephyr" and c.confidence >= 0.9


def test_zephyr_dk_from_cmake_board(tmp_path):
    _write(tmp_path, "prj.conf", "")
    _write(tmp_path, "CMakeLists.txt", "set(BOARD nrf52840dk_nrf52840)\n")
    c = classify_project(tmp_path)
    assert c is not None and c.profile_id == "nrf52840_dk"


def test_zephyr_unknown_board_defaults_low_confidence(tmp_path):
    _write(tmp_path, "prj.conf", "")
    _write(tmp_path, "CMakeLists.txt", "set(BOARD some_unknown_board)\n")
    c = classify_project(tmp_path)
    assert c is not None
    assert c.profile_id == "seeed_xiao_nrf52840_sense" and c.confidence < 0.6


def test_platformio_esp32(tmp_path):
    _write(tmp_path, "platformio.ini", "[env:dev]\nplatform = espressif32\nboard = esp32dev\n")
    c = classify_project(tmp_path)
    assert c is not None
    assert c.profile_id == "esp32_devkitc" and c.framework == "platformio"


def test_platformio_xiao(tmp_path):
    _write(tmp_path, "platformio.ini", "[env:x]\nboard = xiaoblesense_adafruit\n")
    c = classify_project(tmp_path)
    assert c is not None and c.profile_id == "seeed_xiao_nrf52840_sense"


def test_esp_idf_s3(tmp_path):
    _write(tmp_path, "sdkconfig.defaults", "CONFIG_IDF_TARGET_ESP32S3=y\n")
    c = classify_project(tmp_path)
    assert c is not None
    assert c.profile_id == "esp32s3_devkitc" and c.framework == "esp-idf"


def test_rp2040_pico_sdk(tmp_path):
    _write(tmp_path, "pico_sdk_import.cmake", "# pico sdk\n")
    c = classify_project(tmp_path)
    assert c is not None and c.profile_id == "rp_pico"


def test_arduino_sketch_is_indeterminate(tmp_path):
    _write(tmp_path, "blink.ino", "void setup(){} void loop(){}\n")
    assert classify_project(tmp_path) is None


def test_empty_dir_returns_none(tmp_path):
    assert classify_project(tmp_path) is None


def test_import_without_profile_id_classifies(tmp_path):
    _write(tmp_path, "prj.conf", "CONFIG_PRINTK=y\n")
    _write(tmp_path, "CMakeLists.txt", "set(BOARD xiao_ble_sense)\n")
    client = TestClient(create_app())
    resp = client.post("/projects/import", json={"path": str(tmp_path)})
    assert resp.status_code == 200, resp.text
    body = resp.json()
    assert body["classification"]["profile_id"] == "seeed_xiao_nrf52840_sense"
    assert body["metadata"]["profile_id"] == "seeed_xiao_nrf52840_sense"


def test_import_unclassifiable_returns_422(tmp_path):
    client = TestClient(create_app())
    resp = client.post("/projects/import", json={"path": str(tmp_path)})
    assert resp.status_code == 422
