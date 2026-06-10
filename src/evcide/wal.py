"""Append-only telemetry WAL.

Every detection, build, flash, runtime-event-batch, and verification result
is appended as a JSONL record. Provides a durable audit trail the agent can
read back to reason about prior failures during a repair loop.
"""
from __future__ import annotations

import json
import os
import threading
import time
from pathlib import Path
from typing import Any

WAL_PATH = Path(os.environ.get(
    "EVCIDE_WAL_PATH",
    Path.home() / ".evcide" / "wal.jsonl",
))
WAL_PATH.parent.mkdir(parents=True, exist_ok=True)

_lock = threading.Lock()


def append(kind: str, payload: dict[str, Any]) -> None:
    """Append a single record.

    Records are timestamped on write. Caller's payload may contain its own
    domain timestamps; we add a wall-clock `ts_ms` for global ordering.
    """
    record = {
        "ts_ms": int(time.time() * 1000),
        "kind": kind,
        "payload": payload,
    }
    line = json.dumps(record, default=str, ensure_ascii=False)
    with _lock:
        with WAL_PATH.open("a", encoding="utf-8") as f:
            f.write(line + "\n")


def tail(n: int = 50) -> list[dict[str, Any]]:
    if not WAL_PATH.exists():
        return []
    with WAL_PATH.open("r", encoding="utf-8") as f:
        lines = f.readlines()[-n:]
    out: list[dict[str, Any]] = []
    for line in lines:
        try:
            out.append(json.loads(line))
        except Exception:
            continue
    return out
