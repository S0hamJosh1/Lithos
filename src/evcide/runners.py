"""Subprocess runner — captures stdout/stderr/exit code/duration to disk.

Used by every adapter's build/flash methods so log capture, redaction,
and timeouts are uniform. WAL gets a structured record per run.
"""
from __future__ import annotations

import asyncio
import os
import shlex
import time
import uuid
from dataclasses import dataclass
from pathlib import Path

LOG_DIR = Path(os.environ.get("EVCIDE_LOG_DIR", Path.home() / ".evcide" / "logs"))
LOG_DIR.mkdir(parents=True, exist_ok=True)


@dataclass
class SubprocessResult:
    cmd: list[str]
    returncode: int
    stdout: str
    stderr: str
    duration_ms: int
    log_path: str | None  # raw combined-log path


async def run_subprocess(
    cmd: list[str],
    cwd: str | Path | None = None,
    env: dict[str, str] | None = None,
    timeout_s: float | None = 600,
    log_label: str = "run",
) -> SubprocessResult:
    """Run a subprocess and capture everything.

    Stdout and stderr are captured separately so the build runner can parse
    diagnostics from stderr and surface "good" output to the agent.
    A combined merged log is written to LOG_DIR for auditing.
    """
    start = time.perf_counter()
    log_id = f"{log_label}-{int(time.time())}-{uuid.uuid4().hex[:6]}"
    log_path = LOG_DIR / f"{log_id}.log"

    proc = await asyncio.create_subprocess_exec(
        *cmd,
        cwd=str(cwd) if cwd else None,
        env=env,
        stdout=asyncio.subprocess.PIPE,
        stderr=asyncio.subprocess.PIPE,
    )
    try:
        stdout_b, stderr_b = await asyncio.wait_for(proc.communicate(), timeout=timeout_s)
    except asyncio.TimeoutError:
        proc.kill()
        await proc.wait()
        duration_ms = int((time.perf_counter() - start) * 1000)
        log_path.write_text(
            f"$ {' '.join(shlex.quote(c) for c in cmd)}\n"
            f"[TIMEOUT after {timeout_s}s]\n",
            encoding="utf-8",
        )
        return SubprocessResult(
            cmd=cmd, returncode=124, stdout="", stderr=f"timeout after {timeout_s}s",
            duration_ms=duration_ms, log_path=str(log_path),
        )

    duration_ms = int((time.perf_counter() - start) * 1000)
    stdout = stdout_b.decode("utf-8", errors="replace")
    stderr = stderr_b.decode("utf-8", errors="replace")

    log_path.write_text(
        f"$ {' '.join(shlex.quote(c) for c in cmd)}\n"
        f"cwd={cwd!r}\n"
        f"exit={proc.returncode} duration_ms={duration_ms}\n"
        "----- STDOUT -----\n"
        f"{stdout}\n"
        "----- STDERR -----\n"
        f"{stderr}\n",
        encoding="utf-8",
    )

    return SubprocessResult(
        cmd=cmd, returncode=proc.returncode or 0, stdout=stdout, stderr=stderr,
        duration_ms=duration_ms, log_path=str(log_path),
    )
