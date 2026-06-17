"""Runtime output receivers.

Each receiver normalizes hardware behavior into RuntimeEvents that the
Verification Engine consumes. PDF Section 20.

  Hardware/firmware output
    -> Receiver Adapter
    -> Normalized RuntimeEvents
    -> Verification Engine

Serial + File are real implementations. BLE / Socket / ROS are scaffolded
to raise NotImplementedError until their dependencies + hardware are wired.
"""
from __future__ import annotations

import asyncio
import contextlib
import json
import re
from typing import AsyncIterator

from .models import OutputSession, ReceiverKind, RuntimeEvent, now_ms

# Match `key=value` pairs in serial output for parsed-field extraction.
_KV_RE = re.compile(r"(\w+)\s*=\s*(-?\d+(?:\.\d+)?|\w+)")


def _parse_serial_line(line: str) -> dict | None:
    """Best-effort parse of typical embedded log line conventions.

    Tries (in order):
      1) JSON line ({"imu":{...}})  -> dict
      2) `key=val key2=val2` style  -> dict
      3) leading-token tagged ("BOOT_OK", "HEARTBEAT 123")  -> {"tag": ..., "n": ...}

    Returns None if nothing structured found; caller still has raw.
    """
    line = line.strip()
    if not line:
        return None
    if line.startswith("{") and line.endswith("}"):
        try:
            return json.loads(line)
        except Exception:
            pass
    kv = dict(_KV_RE.findall(line))
    if kv:
        parsed: dict[str, float | int | str] = {}
        for k, v in kv.items():
            try:
                parsed[k] = int(v)
            except ValueError:
                try:
                    parsed[k] = float(v)
                except ValueError:
                    parsed[k] = v
        return parsed
    if " " in line:
        head, _, tail = line.partition(" ")
        if head.isidentifier() and tail.strip():
            try:
                return {"tag": head, "n": int(tail)}
            except ValueError:
                return {"tag": head, "value": tail}
    if line.isidentifier():
        return {"tag": line}
    return None


# ============ Serial ============

async def serial_receiver_stream(
    session: OutputSession, stop_event: asyncio.Event
) -> AsyncIterator[RuntimeEvent]:
    """Pull lines from a serial port and yield RuntimeEvents.

    Uses pyserial-asyncio if available; falls back to a thread-backed pyserial
    reader otherwise (works on Windows where pyserial-asyncio is sometimes flaky).
    """
    cfg = session.config
    port = cfg.serial_port
    baud = cfg.serial_baud or 115200
    if not port:
        raise ValueError("serial receiver requires config.serial_port")

    try:
        import serial as pyserial  # noqa: F401
    except ImportError as e:
        raise RuntimeError("pyserial not installed; pip install pyserial") from e

    # Threaded read into an asyncio.Queue. Robust on Windows.
    queue: asyncio.Queue[bytes] = asyncio.Queue(maxsize=4096)
    loop = asyncio.get_running_loop()

    def _reader():
        import serial as ps
        from serial import SerialException
        try:
            ser = ps.Serial(port, baud, timeout=0.1)
        except SerialException:
            loop.call_soon_threadsafe(queue.put_nowait, b"")
            return
        try:
            buf = bytearray()
            while not stop_event.is_set():
                try:
                    chunk = ser.read(256)
                except SerialException:
                    break
                if not chunk:
                    continue
                buf.extend(chunk)
                while b"\n" in buf:
                    line, _, rest = buf.partition(b"\n")
                    buf = bytearray(rest)
                    asyncio.run_coroutine_threadsafe(queue.put(bytes(line)), loop)
        finally:
            with contextlib.suppress(Exception):
                ser.close()

    reader_task = loop.run_in_executor(None, _reader)
    try:
        while not stop_event.is_set():
            try:
                line = await asyncio.wait_for(queue.get(), timeout=0.25)
            except asyncio.TimeoutError:
                continue
            if line == b"":
                break
            try:
                text = line.decode("utf-8", errors="replace").rstrip("\r")
            except Exception:
                text = ""
            parsed = _parse_serial_line(text)
            yield RuntimeEvent(
                source=ReceiverKind.SERIAL,
                timestamp_ms=now_ms(),
                board_id=session.board_id,
                stream_id=session.stream_id,
                type="line",
                raw=text,
                parsed=parsed,
                metadata={"port": port, "baud": baud},
            )
    finally:
        stop_event.set()
        with contextlib.suppress(Exception):
            await asyncio.wait_for(reader_task, timeout=1.0)


# ============ File ============

async def file_receiver_stream(
    session: OutputSession, stop_event: asyncio.Event
) -> AsyncIterator[RuntimeEvent]:
    """Tail a CSV/log file written by a helper script or ROS rosbag dumper.

    Each non-empty line becomes a "row" event with parsed key=val or csv columns.
    """
    cfg = session.config
    path = cfg.file_path
    if not path:
        raise ValueError("file receiver requires config.file_path")
    pos = 0
    while not stop_event.is_set():
        try:
            with open(path, encoding="utf-8") as f:
                f.seek(pos)
                for line in f:
                    if stop_event.is_set():
                        break
                    line = line.rstrip("\n")
                    if not line:
                        continue
                    parsed = _parse_serial_line(line)
                    if parsed is None and "," in line:
                        cols = [c.strip() for c in line.split(",")]
                        parsed = {f"c{i}": c for i, c in enumerate(cols)}
                    yield RuntimeEvent(
                        source=ReceiverKind.FILE,
                        timestamp_ms=now_ms(),
                        board_id=session.board_id,
                        stream_id=session.stream_id,
                        type="row",
                        raw=line,
                        parsed=parsed,
                        metadata={"path": path},
                    )
                pos = f.tell()
        except FileNotFoundError:
            await asyncio.sleep(0.5)
            continue
        await asyncio.sleep(0.2)


# ============ BLE / Socket / ROS — STUBS ============

async def ble_receiver_stream(
    session: OutputSession, stop_event: asyncio.Event
) -> AsyncIterator[RuntimeEvent]:
    """BLE receiver — STUB.

    Real impl: use `bleak` to scan for advertisements + connect + subscribe
    to GATT characteristics named in config.ble_filter. Yield advertisement,
    service, characteristic, packet, RSSI events per PDF Section 20.
    """
    raise NotImplementedError(
        "BLE receiver not implemented yet. Install `pip install evcide[ble]` "
        "and wire bleak per the PDF Section 24 spec."
    )
    if False:                                    # pragma: no cover  (typing)
        yield                                    # type: ignore


async def socket_receiver_stream(
    session: OutputSession, stop_event: asyncio.Event
) -> AsyncIterator[RuntimeEvent]:
    """TCP/UDP/WebSocket receiver — STUB.

    Real impl: open the configured socket, push each frame as a packet event.
    For HTTP health endpoints, poll on an interval and emit health-check events.
    """
    raise NotImplementedError("Socket receiver not implemented yet")
    if False:                                    # pragma: no cover
        yield                                    # type: ignore


async def ros_receiver_stream(
    session: OutputSession, stop_event: asyncio.Event
) -> AsyncIterator[RuntimeEvent]:
    """ROS topic receiver — STUB.

    Real impl: roslibpy bridge to rosbridge_server, subscribe to config.ros_topic,
    emit one event per message with parsed fields + frame id + topic rate metadata.
    """
    raise NotImplementedError(
        "ROS receiver not implemented yet. Install `pip install evcide[ros]` "
        "and wire roslibpy per the PDF Section 25 spec."
    )
    if False:                                    # pragma: no cover
        yield                                    # type: ignore


# ============ Dispatcher ============

def get_receiver_stream(kind: ReceiverKind):
    return {
        ReceiverKind.SERIAL: serial_receiver_stream,
        ReceiverKind.FILE: file_receiver_stream,
        ReceiverKind.BLE: ble_receiver_stream,
        ReceiverKind.SOCKET: socket_receiver_stream,
        ReceiverKind.ROS_TOPIC: ros_receiver_stream,
    }[kind]
