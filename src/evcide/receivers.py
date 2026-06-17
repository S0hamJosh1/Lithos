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

# Max seconds to wait for a socket connection before declaring the host unreachable.
_SOCKET_CONNECT_TIMEOUT_S = 5.0


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


# ============ BLE ============

async def ble_receiver_stream(
    session: OutputSession, stop_event: asyncio.Event
) -> AsyncIterator[RuntimeEvent]:
    """BLE receiver via `bleak`.

    config.ble_filter keys (all optional):
      name                  match if advertised local name contains this
      address               match an exact device address
      service_uuids         restrict the scan to these advertised service UUIDs
      characteristic_uuid   if set, connect to the matched device and stream
                            notifications from this GATT characteristic

    Events emitted: ``advertisement`` per matching advert; then (only when a
    characteristic_uuid is given) ``connect`` with the device's service-uuid list,
    ``packet`` per notification, or ``unreachable`` if the connection fails.

    NOTE: HARDWARE-UNTESTED. The advertisement/connect/packet event SHAPES are
    covered by verify-engine tests with synthetic events, but this bleak code path
    has not been run against a real radio. Validate on a XIAO before relying on it.
    """
    cfg = session.config
    flt = cfg.ble_filter or {}
    name_match = flt.get("name")
    address_match = flt.get("address")
    service_uuids = flt.get("service_uuids")
    char_uuid = flt.get("characteristic_uuid")

    try:
        from bleak import BleakClient, BleakScanner
    except ImportError as e:
        raise RuntimeError("bleak not installed; pip install evcide[ble]") from e

    adv_queue: asyncio.Queue = asyncio.Queue(maxsize=1024)
    matched: dict = {"device": None}

    def _matches(device, adv) -> bool:
        if address_match and (device.address or "").lower() != address_match.lower():
            return False
        if name_match:
            nm = adv.local_name or device.name or ""
            if name_match.lower() not in nm.lower():
                return False
        return True

    def _on_detect(device, adv):
        ev = RuntimeEvent(
            source=ReceiverKind.BLE, timestamp_ms=now_ms(),
            board_id=session.board_id, stream_id=session.stream_id,
            type="advertisement", raw=adv.local_name or device.name,
            parsed={
                "name": adv.local_name or device.name,
                "address": device.address,
                "rssi": adv.rssi,
                "service_uuids": list(adv.service_uuids or []),
            },
        )
        if _matches(device, adv):
            if matched["device"] is None:
                matched["device"] = device
            with contextlib.suppress(asyncio.QueueFull):
                adv_queue.put_nowait(ev)

    scanner = BleakScanner(detection_callback=_on_detect, service_uuids=service_uuids)
    await scanner.start()
    try:
        while not stop_event.is_set():
            try:
                ev = await asyncio.wait_for(adv_queue.get(), timeout=0.25)
            except asyncio.TimeoutError:
                if char_uuid and matched["device"] is not None:
                    break
                continue
            yield ev
            if char_uuid and matched["device"] is not None:
                break
    finally:
        with contextlib.suppress(Exception):
            await scanner.stop()

    if not (char_uuid and matched["device"] and not stop_event.is_set()):
        return

    # Connect + subscribe to the requested notify characteristic.
    device = matched["device"]
    pkt_queue: asyncio.Queue = asyncio.Queue(maxsize=4096)

    def _on_notify(_sender, data: bytearray):
        text = bytes(data).decode("utf-8", errors="replace")
        ev = RuntimeEvent(
            source=ReceiverKind.BLE, timestamp_ms=now_ms(),
            board_id=session.board_id, stream_id=session.stream_id,
            type="packet", raw=text, parsed=_parse_serial_line(text),
            metadata={"characteristic": char_uuid},
        )
        with contextlib.suppress(asyncio.QueueFull):
            pkt_queue.put_nowait(ev)

    try:
        async with BleakClient(device) as client:
            yield RuntimeEvent(
                source=ReceiverKind.BLE, timestamp_ms=now_ms(),
                board_id=session.board_id, stream_id=session.stream_id,
                type="connect", raw=None,
                parsed={
                    "address": device.address,
                    "service_uuids": [str(s.uuid) for s in client.services],
                },
            )
            await client.start_notify(char_uuid, _on_notify)
            try:
                while not stop_event.is_set():
                    try:
                        yield await asyncio.wait_for(pkt_queue.get(), timeout=0.25)
                    except asyncio.TimeoutError:
                        continue
            finally:
                with contextlib.suppress(Exception):
                    await client.stop_notify(char_uuid)
    except Exception as e:
        yield RuntimeEvent(
            source=ReceiverKind.BLE, timestamp_ms=now_ms(),
            board_id=session.board_id, stream_id=session.stream_id,
            type="unreachable", raw=f"BLE connect failed: {e}",
        )


# ============ Socket / ROS ============


async def socket_receiver_stream(
    session: OutputSession, stop_event: asyncio.Event
) -> AsyncIterator[RuntimeEvent]:
    """TCP line receiver — for WiFi/Ethernet boards (e.g. ESP32) that stream
    newline-delimited telemetry over a socket.

    Yields a ``connect`` event on a successful connection (so a reachable-but-
    silent endpoint still proves reachability), then one ``line`` event per
    newline-delimited frame. On connection failure it yields a single
    ``unreachable`` event and returns, so a socket_reachable check fails with
    evidence instead of looking like a silent no-data timeout.
    """
    cfg = session.config
    host = cfg.socket_host
    port = cfg.socket_port
    if not host or not port:
        raise ValueError("socket receiver requires config.socket_host and socket_port")

    # Bound the connect so a blackholed host (board not on the network) surfaces as
    # an unreachable event quickly, instead of hanging until the verification times
    # out (which would look like a silent no-data result).
    try:
        reader, writer = await asyncio.wait_for(
            asyncio.open_connection(host, port), timeout=_SOCKET_CONNECT_TIMEOUT_S
        )
    except (OSError, asyncio.TimeoutError) as e:
        reason = "connect timeout" if isinstance(e, asyncio.TimeoutError) else str(e)
        yield RuntimeEvent(
            source=ReceiverKind.SOCKET, timestamp_ms=now_ms(),
            board_id=session.board_id, stream_id=session.stream_id,
            type="unreachable", raw=reason, metadata={"host": host, "port": port},
        )
        return

    yield RuntimeEvent(
        source=ReceiverKind.SOCKET, timestamp_ms=now_ms(),
        board_id=session.board_id, stream_id=session.stream_id,
        type="connect", raw=None, metadata={"host": host, "port": port},
    )
    try:
        while not stop_event.is_set():
            try:
                raw = await asyncio.wait_for(reader.readline(), timeout=0.25)
            except asyncio.TimeoutError:
                continue
            if not raw:
                break  # EOF — peer closed the connection
            text = raw.decode("utf-8", errors="replace").rstrip("\r\n")
            if not text:
                continue
            yield RuntimeEvent(
                source=ReceiverKind.SOCKET, timestamp_ms=now_ms(),
                board_id=session.board_id, stream_id=session.stream_id,
                type="line", raw=text, parsed=_parse_serial_line(text),
                metadata={"host": host, "port": port},
            )
    finally:
        writer.close()
        with contextlib.suppress(Exception):
            await writer.wait_closed()


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
