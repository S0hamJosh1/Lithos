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
# NOTE: intentionally NO `from __future__ import annotations` here. The request
# body models are defined inside create_app(); stringized annotations would be
# unresolvable by FastAPI (it resolves them against module globals), making every
# POST endpoint silently treat its body model as a query param. Real annotations
# keep them introspectable.
import contextlib

from fastapi import FastAPI, HTTPException, WebSocket, WebSocketDisconnect
from pydantic import BaseModel

from . import wal
from .adapters import detect_all_boards, get_adapter_for_profile
from .eventbus import bus
from . import workspace
from .dsl import DSLError, parse_contract
from .mutation import assess_contract, assess_minimality
from .verify import run_verification
from .models import (
    DetectedBoard,
    MinimalityReport,
    MutationReport,
    OutputConfig,
    ProjectConfig,
    RuntimeEvent,
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
        result = await workspace.create_project(ProjectConfig(**body.model_dump()))
        wal.append("project.create", {"profile_id": body.profile_id,
                                      "success": result.success,
                                      "root": result.root})
        return result

    class ImportProjectBody(BaseModel):
        path: str
        profile_id: str | None = None

    @app.post("/projects/import")
    async def import_project(body: ImportProjectBody):
        try:
            meta, classification = await workspace.import_project(body.path, body.profile_id)
        except workspace.ClassificationError as e:
            raise HTTPException(422, str(e)) from None
        except NotImplementedError as e:
            raise HTTPException(501, str(e)) from None
        wal.append("project.import", {
            **meta.model_dump(),
            "classified": classification.model_dump() if classification else None,
        })
        return {"metadata": meta, "classification": classification}

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
        #: Optional correlation id. Open a WS to /streams/{stream_id} first, then
        #: POST /verify with the same id to watch the run live.
        stream_id: str | None = None

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

        # Live streaming: publish events + result to the bus under the caller's
        # correlation id. The verify engine is adapter-agnostic, so we call it
        # directly (passing the adapter for repair hints) to thread the sink in.
        topic = body.stream_id
        sink = None
        if topic:
            bus.reset(topic)

            async def sink(msg, _t=topic):
                await bus.publish(_t, msg)

        try:
            result = await run_verification(
                session, body.contract, adapter=adapter, sink=sink
            )
        finally:
            if topic:
                bus.close(topic)
        wal.append("verify", {
            "contract_id": body.contract.id,
            "status": result.status,
            "failure_classification": result.failure_classification,
            "summary": result.agent_summary,
        })
        return result

    # ----- Contract authoring (DSL → contract) -----

    class ParseBody(BaseModel):
        text: str
        id: str = "dsl-contract"
        target: str = ""
        receiver: str = "serial"
        timeout_ms: int = 10000

    @app.post("/contracts/parse", response_model=VerificationContract)
    async def parse(body: ParseBody) -> VerificationContract:
        try:
            return parse_contract(
                body.text, id=body.id, target=body.target,
                receiver=body.receiver, timeout_ms=body.timeout_ms,
            )
        except DSLError as e:
            raise HTTPException(400, str(e)) from None

    # ----- Contract meaningfulness (break-on-purpose) -----

    class AssessBody(BaseModel):
        contract: VerificationContract
        #: A baseline event stream that PASSES the contract. The frontend captures
        #: this from a known-good run; mutation testing breaks it on purpose to
        #: prove the contract actually catches the failure.
        baseline_events: list[RuntimeEvent]

    @app.post("/contracts/assess", response_model=MutationReport)
    async def assess(body: AssessBody) -> MutationReport:
        report = assess_contract(body.contract, body.baseline_events)
        wal.append("assess", {
            "contract_id": body.contract.id,
            "meaningful": report.meaningful,
            "score": report.score,
            "survived": report.survived,
        })
        return report

    @app.post("/contracts/minimality", response_model=MinimalityReport)
    async def minimality(body: AssessBody) -> MinimalityReport:
        # Leave-one-out dual of /assess: is every expectation load-bearing, or
        # does the contract carry redundant / non-covering checks? Same input
        # shape (contract + a PASSING baseline stream).
        report = assess_minimality(body.contract, body.baseline_events)
        wal.append("minimality", {
            "contract_id": body.contract.id,
            "minimal": report.minimal,
            "redundant": report.redundant,
        })
        return report

    # ----- WAL -----

    @app.get("/wal/tail")
    async def wal_tail(n: int = 50):
        return wal.tail(n=n)

    # ----- Live stream -----

    @app.websocket("/streams/{stream_id}")
    async def stream(ws: WebSocket, stream_id: str):
        """Forward every bus message for this correlation id to the frontend.

        Replays anything already published (so connecting a beat after /verify
        starts still delivers early events), then streams live until the topic
        is closed at end-of-verify.
        """
        await ws.accept()
        try:
            with contextlib.suppress(WebSocketDisconnect):
                async for msg in bus.subscribe(stream_id):
                    await ws.send_json(msg)
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
