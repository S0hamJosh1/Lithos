"""End-to-end API test: a verify run streams live over the WebSocket.

No hardware: board detection and the serial receiver are monkeypatched with a
synthetic in-memory stream. This proves the actual product behavior the frontend
depends on — open WS, POST /verify with the same correlation id, watch the run.
"""
from __future__ import annotations

import asyncio
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))

import pytest
from fastapi.testclient import TestClient

from evcide import receivers as rcv
from evcide.adapters.registry import get_adapter_for_profile
from evcide.api import create_app
from evcide.models import BoardFamily, DetectedBoard, ReceiverKind, RuntimeEvent

PROFILE = "seeed_xiao_nrf52840_sense"


def _ev(text: str, t_ms: int, parsed=None) -> RuntimeEvent:
    return RuntimeEvent(source=ReceiverKind.SERIAL, timestamp_ms=t_ms,
                        board_id="fake-1", stream_id="strm", type="line",
                        raw=text, parsed=parsed)


def _synthetic(events):
    async def stream(session, stop_event):
        for ev in events:
            if stop_event.is_set():
                return
            yield ev
            await asyncio.sleep(0)
    return stream


@pytest.fixture
def patched(monkeypatch):
    adapter = get_adapter_for_profile(PROFILE)
    fake = DetectedBoard(id="fake-1", profile_id=PROFILE, family=BoardFamily.NRF52,
                         serial_port="/dev/null")

    async def fake_detect():
        return [fake]

    monkeypatch.setattr(adapter, "detect", fake_detect)
    events = [_ev("BOOT_OK", 100), _ev("HEARTBEAT 1", 1100), _ev("HEARTBEAT 2", 2100)]
    monkeypatch.setattr(rcv, "get_receiver_stream", lambda k: _synthetic(events))
    return adapter


def test_verify_streams_events_and_result_over_ws(patched):
    app = create_app()
    client = TestClient(app)
    sid = "corr-test-1"
    contract = {
        "id": "blink", "target": PROFILE, "timeout_ms": 2000,
        "receivers": [{"type": "serial", "port": "auto", "baud": 115200}],
        "expectations": [
            {"kind": "contains", "pattern": "BOOT_OK"},
            {"kind": "count_min", "pattern": "HEARTBEAT", "count": 2},
        ],
    }

    with client.websocket_connect(f"/streams/{sid}") as ws:
        resp = client.post("/verify", json={
            "profile_id": PROFILE, "board_id": "fake-1",
            "stream_id": sid, "contract": contract,
        })
        assert resp.status_code == 200, resp.text
        assert resp.json()["status"] == "pass"

        kinds, last = [], None
        for _ in range(50):  # guard against hang
            msg = ws.receive_json()
            kinds.append(msg["type"])
            if msg["type"] == "result":
                last = msg
                break

    assert "event" in kinds, kinds
    assert last is not None and last["data"]["status"] == "pass"


def test_verify_404_when_board_absent(patched, monkeypatch):
    adapter = patched

    async def empty():
        return []

    monkeypatch.setattr(adapter, "detect", empty)
    app = create_app()
    client = TestClient(app)
    resp = client.post("/verify", json={
        "profile_id": PROFILE, "board_id": "nope",
        "contract": {"id": "x", "target": PROFILE,
                     "receivers": [{"type": "serial", "port": "auto"}],
                     "expectations": []},
    })
    assert resp.status_code == 404
