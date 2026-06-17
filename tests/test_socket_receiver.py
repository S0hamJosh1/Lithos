"""Loopback tests for the TCP socket receiver + socket_reachable check.

A real asyncio server on an ephemeral localhost port stands in for a WiFi board,
so the full receiver -> verify pipeline is exercised with no hardware and no
monkeypatching of the receiver itself.
"""
import asyncio
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))

import pytest

from evcide.models import (
    Expectation, ExpectationKind, OutputConfig, OutputSession, ReceiverDef,
    ReceiverKind, VerificationContract, now_ms,
)
from evcide.verify import run_verification


def _session(host: str, port: int) -> OutputSession:
    return OutputSession(
        stream_id="strm-sock", receiver=ReceiverKind.SOCKET, board_id="sock-board",
        started_at_ms=now_ms(),
        config=OutputConfig(receiver=ReceiverKind.SOCKET, socket_host=host, socket_port=port),
    )


async def _start_server(lines: list[str]):
    async def handle(reader, writer):
        for ln in lines:
            writer.write((ln + "\n").encode())
            await writer.drain()
        await asyncio.sleep(0.05)
        writer.close()

    server = await asyncio.start_server(handle, "127.0.0.1", 0)
    port = server.sockets[0].getsockname()[1]
    return server, port


@pytest.mark.asyncio
async def test_socket_reachable_and_line_pass():
    server, port = await _start_server(["BOOT_OK", "HEARTBEAT 1"])
    async with server:
        contract = VerificationContract(
            id="sk1", target="esp32_devkitc", timeout_ms=2000,
            receivers=[ReceiverDef(type=ReceiverKind.SOCKET,
                                   socket={"host": "127.0.0.1", "port": port})],
            expectations=[
                Expectation(kind=ExpectationKind.SOCKET_REACHABLE),
                Expectation(kind=ExpectationKind.CONTAINS, pattern="BOOT_OK"),
            ],
        )
        result = await run_verification(_session("127.0.0.1", port), contract)
    assert result.status == "pass", result.agent_summary


@pytest.mark.asyncio
async def test_socket_unreachable_fails():
    # Bind then immediately close to obtain a port that refuses connections.
    server, port = await _start_server([])
    server.close()
    await server.wait_closed()

    # Budget must exceed the OS connection-refused latency (≈2s on Windows).
    contract = VerificationContract(
        id="sk2", target="esp32_devkitc", timeout_ms=6000,
        receivers=[ReceiverDef(type=ReceiverKind.SOCKET,
                               socket={"host": "127.0.0.1", "port": port})],
        expectations=[Expectation(kind=ExpectationKind.SOCKET_REACHABLE)],
    )
    result = await run_verification(_session("127.0.0.1", port), contract)
    assert result.status == "fail"
    assert "unreachable" in result.checks[0].message
