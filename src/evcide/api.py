"""FastAPI HTTP + WebSocket surface for the Void IDE frontend.

Endpoints map directly to the agent loop (PDF Section 17):
  GET  /boards                      detection
  POST /projects/create             scaffold a project
  POST /projects/import             classify an existing project
  POST /build                       trigger build
  POST /flash                       trigger flash
  POST /verify                      run a verification contract
  WS   /streams/{stream_id}         live RuntimeEvents (and BuildResult /
                                    FlashResult / VerificationResult)
  GET  /wal/tail                    inspect telemetry
"""
from __future__ import annotations

import asyncio
import contextlib

from fastapi import FastAPI, HTTPException, WebSocket, WebSocketDisconnect
from pydantic import BaseModel

from . import wal
from .adapters import detect_all_boards, get_adapter_for_profile
from .models import (
    DetectedBoard,
    OutputConfig,
    ProjectConfig,
    VerificationContract,
)


def create_app() -> FastAPI:
    app = FastAPI(
        title="Lithos / evcide",
        version="0.1.0",
        description="Embedded Vibe-Coding IDE backend",
    )

    # ----- Detection -----

    @app.get("/boards", response_model=list[DetectedBoard])
    async def list_boards() -> list[DetectedBoard]:
        boards = await detect_all_boards()
        wal.append("detect", {"count": len(boards),
                              "boards": [b.model_dump() for b in boards]})
        return boards

    # ----- Project -----

    class CreateProjectBody(BaseModel):
        profile_id: str
        name: str
        framework: str
        target_dir: str
        template: str | None = None
        options: dict = {}

    @app.post("/projects/create")
    async def create_project(body: CreateProjectBody):
        adapter = get_adapter_for_profile(body.profile_id)
        cfg = ProjectConfig(**body.model_dump())
        result = await adapter.create_project(cfg)
        wal.append("project.create", {"profile_id": body.profile_id,
                                      "success": result.success,
                                      "root": result.root})
        return result

    class ImportProjectBody(BaseModel):
        path: str
        profile_id: str | None = None

    @app.post("/projects/import")
    async def import_project(body: ImportProjectBody):
        # If profile_id is omitted, we'd need a project classifier (planned).
        if not body.profile_id:
            raise HTTPException(400, "profile_id is required until project classifier ships")
        adapter = get_adapter_for_profile(body.profile_id)
        meta = await adapter.import_project(body.path)
        wal.append("project.import", meta.model_dump())
        return meta

    # ----- Build -----

    class BuildBody(BaseModel):
        profile_id: str
        project_path: str

    @app.post("/build")
    async def build(body: BuildBody):
        adapter = get_adapter_for_profile(body.profile_id)
        result = await adapter.build(body.project_path)
        wal.append("build", result.model_dump())
        return result

    # ----- Flash -----

    class FlashBody(BaseModel):
        profile_id: str
        project_path: str
        board_id: str

    @app.post("/flash")
    async def flash(body: FlashBody):
        adapter = get_adapter_for_profile(body.profile_id)
        boards = await adapter.detect()
        match = next((b for b in boards if b.id == body.board_id), None)
        if not match:
            raise HTTPException(404, f"board {body.board_id!r} not currently detected")
        result = await adapter.flash(body.project_path, match)
        wal.append("flash", result.model_dump())
        return result

    # ----- Verify -----

    class VerifyBody(BaseModel):
        profile_id: str
        board_id: str
        contract: VerificationContract

    @app.post("/verify")
    async def verify(body: VerifyBody):
        adapter = get_adapter_for_profile(body.profile_id)
        boards = await adapter.detect()
        match = next((b for b in boards if b.id == body.board_id), None)
        if not match:
            raise HTTPException(404, f"board {body.board_id!r} not currently detected")
        if not body.contract.receivers:
            raise HTTPException(400, "contract.receivers must be non-empty")
        rdef = body.contract.receivers[0]
        cfg = OutputConfig(
            receiver=rdef.type,
            serial_port=(None if rdef.port == "auto" else rdef.port),
            serial_baud=rdef.baud,
            ble_filter=rdef.ble_filter,
            socket_host=(rdef.socket or {}).get("host"),
            socket_port=(rdef.socket or {}).get("port"),
            ros_topic=rdef.ros_topic,
            file_path=rdef.file_path,
        )
        session = await adapter.open_output_channel(match, cfg)
        result = await adapter.verify(session, body.contract)
        wal.append("verify", {
            "contract_id": body.contract.id,
            "status": result.status,
            "failure_classification": result.failure_classification,
            "summary": result.agent_summary,
        })
        return result

    # ----- WAL -----

    @app.get("/wal/tail")
    async def wal_tail(n: int = 50):
        return wal.tail(n=n)

    # ----- Live stream (placeholder until full event-bus wiring) -----

    @app.websocket("/streams/{stream_id}")
    async def stream(ws: WebSocket, stream_id: str):
        await ws.accept()
        try:
            # MVP: send WAL records as they appear. Full impl: bridge into a
            # per-session pub/sub so verify can stream events live.
            with contextlib.suppress(WebSocketDisconnect):
                last_n = 0
                while True:
                    records = wal.tail(n=200)
                    if len(records) > last_n:
                        for r in records[last_n:]:
                            await ws.send_json(r)
                        last_n = len(records)
                    await asyncio.sleep(0.5)
        except WebSocketDisconnect:
            return

    return app


def main() -> None:
    """Console entry point: `evcide-api`."""
    import uvicorn
    uvicorn.run("evcide.api:create_app", factory=True, host="127.0.0.1", port=7878,
                reload=False, log_level="info")


if __name__ == "__main__":
    main()
