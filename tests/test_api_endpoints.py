"""API tests for the boards / create / build / flash / wal endpoints.

Hardware-free: detection returns nothing on a dev box, project creation writes
real files to a tmp dir, and build/flash exercise the graceful no-`west` path.
"""
import shutil
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))

import pytest
from fastapi.testclient import TestClient

from evcide.adapters.registry import get_adapter_for_profile
from evcide.api import create_app
from evcide.models import BoardFamily, DetectedBoard

PROFILE = "seeed_xiao_nrf52840_sense"
WEST = shutil.which("west")


def test_boards_returns_list():
    client = TestClient(create_app())
    resp = client.get("/boards")
    assert resp.status_code == 200
    assert isinstance(resp.json(), list)


def test_create_project_writes_files(tmp_path):
    client = TestClient(create_app())
    target = tmp_path / "proj"
    resp = client.post("/projects/create", json={
        "profile_id": PROFILE, "name": "blink", "framework": "zephyr",
        "target_dir": str(target),
    })
    assert resp.status_code == 200, resp.text
    body = resp.json()
    assert body["success"] is True
    assert (target / "prj.conf").exists()
    assert (target / "src" / "main.c").exists()


@pytest.mark.skipif(WEST is not None, reason="west is installed; skip no-west path")
def test_build_without_west_reports_127(tmp_path):
    client = TestClient(create_app())
    resp = client.post("/build", json={"profile_id": PROFILE, "project_path": str(tmp_path)})
    assert resp.status_code == 200, resp.text
    body = resp.json()
    assert body["success"] is False
    assert body["exit_code"] == 127
    assert "west" in body["summary"].lower()


def test_flash_404_when_board_absent():
    client = TestClient(create_app())
    resp = client.post("/flash", json={
        "profile_id": PROFILE, "project_path": ".", "board_id": "nope",
    })
    assert resp.status_code == 404


@pytest.mark.skipif(WEST is not None, reason="west is installed; skip no-west path")
def test_flash_with_board_but_no_west(monkeypatch, tmp_path):
    adapter = get_adapter_for_profile(PROFILE)
    fake = DetectedBoard(id="fake-1", profile_id=PROFILE, family=BoardFamily.NRF52,
                         serial_port="/dev/null")

    async def fake_detect():
        return [fake]

    monkeypatch.setattr(adapter, "detect", fake_detect)
    client = TestClient(create_app())
    resp = client.post("/flash", json={
        "profile_id": PROFILE, "project_path": str(tmp_path), "board_id": "fake-1",
    })
    assert resp.status_code == 200, resp.text
    assert resp.json()["success"] is False


def test_wal_tail_returns_list():
    client = TestClient(create_app())
    resp = client.get("/wal/tail?n=5")
    assert resp.status_code == 200
    assert isinstance(resp.json(), list)


def test_assess_endpoint_flags_theater_and_certifies_meaningful():
    client = TestClient(create_app())

    def ev(text="", t=0, parsed=None):
        return {
            "source": "serial", "timestamp_ms": t, "board_id": "b",
            "stream_id": "s", "type": "line", "raw": text, "parsed": parsed,
        }

    receivers = [{"type": "serial"}]

    # Meaningful: a boot-token check whose deliberate break is caught.
    good = client.post("/contracts/assess", json={
        "contract": {
            "id": "good", "target": "t", "receivers": receivers,
            "expectations": [{"kind": "contains", "pattern": "BOOT_OK"}],
        },
        "baseline_events": [ev("BOOT_OK", 0)],
    })
    assert good.status_code == 200, good.text
    assert good.json()["meaningful"] is True

    # Theater: an unbounded field_range passes any value → a surviving mutant.
    theater = client.post("/contracts/assess", json={
        "contract": {
            "id": "theater", "target": "t", "receivers": receivers,
            "expectations": [{"kind": "field_range", "field": "temp"}],
        },
        "baseline_events": [ev("t", i * 100, {"temp": 20.0}) for i in range(3)],
    })
    assert theater.status_code == 200, theater.text
    body = theater.json()
    assert body["meaningful"] is False
    assert body["survived"] >= 1


def test_parse_endpoint_builds_contract_and_400s_on_bad_dsl():
    client = TestClient(create_app())
    ok = client.post("/contracts/parse", json={
        "text": "@id boot\ncontains BOOT_OK within_ms:3000\nno_timeout 1500",
        "target": "seeed_xiao_nrf52840_sense",
    })
    assert ok.status_code == 200, ok.text
    body = ok.json()
    assert body["id"] == "boot"
    assert len(body["expectations"]) == 2

    bad = client.post("/contracts/parse", json={"text": "frobnicate X"})
    assert bad.status_code == 400
    assert "unknown directive" in bad.json()["detail"]
