"""Embedded Vibe-Coding IDE backend.

Autonomous embedded firmware loop with runtime verification.
Closes the loop: edit -> build -> flash -> observe -> verify -> diagnose -> repair.

The moat is runtime verification, not flashing.
"""
__version__ = "0.1.0"

from . import models  # noqa: F401
