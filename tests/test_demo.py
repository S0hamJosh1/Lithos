"""Smoke test: the offline demo runs end-to-end and the loop closes."""
from __future__ import annotations

import asyncio
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "scripts"))
sys.path.insert(0, str(ROOT / "src"))


def test_demo_loop_runs_and_closes(capsys):
    import demo_loop

    rc = asyncio.run(demo_loop.main())
    assert rc == 0
    out = capsys.readouterr().out
    assert "verdict: FAIL" in out          # starts broken
    assert "re-verify verdict: PASS" in out  # repaired to passing
    assert "meaningful: True" in out         # and the contract is not theater
