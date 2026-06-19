"""Capture & replay — record a live event stream to JSONL, replay it offline.

The replay half of the moat without a board: capture a real hardware run ONCE
(attach `CaptureSink` to `run_verification`), then feed `load_events()` into
`evaluate_events` / `assess_contract` forever — offline regression and
break-on-purpose baselines, no board needed again.

Faithful by design: the original `timestamp_ms` / `type` / `raw` / `parsed` are
preserved, because timing checks (`within_ms`, rate, `no_timeout`) depend on
them. This is distinct from the line-tailing `file_receiver_stream`, which
re-stamps wall-clock and is for raw CSV/log files, not captured events.
"""
from __future__ import annotations

import json
from pathlib import Path

from .models import RuntimeEvent


def dump_events(path: str | Path, events: list[RuntimeEvent]) -> int:
    """Write events to a JSONL capture file (one event per line). Returns the count."""
    p = Path(path)
    with p.open("w", encoding="utf-8") as f:
        for ev in events:
            f.write(ev.model_dump_json() + "\n")
    return len(events)


def load_events(path: str | Path) -> list[RuntimeEvent]:
    """Load a JSONL capture back into faithful RuntimeEvents (timestamps intact).

    Feeds straight into `evaluate_events(contract, load_events(path))` and
    `assess_contract(contract, load_events(path))`.
    """
    out: list[RuntimeEvent] = []
    for line in Path(path).read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if line:
            out.append(RuntimeEvent.model_validate_json(line))
    return out


class CaptureSink:
    """An async sink for `run_verification(..., sink=CaptureSink(path))` that
    records the live stream to JSONL as it arrives, so a real run can be replayed
    offline. Records `event` messages only; the final `result` is skipped.

    Use as a context manager, or remember to `close()`:
        with CaptureSink("run.jsonl") as cap:
            await run_verification(session, contract, sink=cap)
        events = load_events("run.jsonl")
    """

    def __init__(self, path: str | Path):
        self.path = Path(path)
        self._f = self.path.open("w", encoding="utf-8")
        self.count = 0

    async def __call__(self, msg: dict) -> None:
        if msg.get("type") == "event":
            self._f.write(json.dumps(msg["data"]) + "\n")
            self._f.flush()
            self.count += 1

    def close(self) -> None:
        if not self._f.closed:
            self._f.close()

    def __enter__(self) -> "CaptureSink":
        return self

    def __exit__(self, *exc) -> None:
        self.close()
