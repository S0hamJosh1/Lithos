"""Adapter registry — discovers all board adapters and runs detection across them.

Detection is union: every adapter is invoked, results combined.
"""
from __future__ import annotations

import asyncio

from ..models import BoardFamily, DetectedBoard
from .base import BoardAdapter
from .esp32 import ESP32Adapter
from .nrf52 import NRF52Adapter
from .rp2040 import RP2040Adapter
from .stm32 import STM32Adapter

_ADAPTERS: dict[BoardFamily, BoardAdapter] = {
    BoardFamily.NRF52: NRF52Adapter(),
    BoardFamily.STM32: STM32Adapter(),
    BoardFamily.ESP32: ESP32Adapter(),
    BoardFamily.RP2040: RP2040Adapter(),
}


def list_adapters() -> list[BoardAdapter]:
    return list(_ADAPTERS.values())


def get_adapter(family: BoardFamily | str) -> BoardAdapter:
    key = BoardFamily(family) if isinstance(family, str) else family
    try:
        return _ADAPTERS[key]
    except KeyError as e:
        raise KeyError(f"no adapter registered for family {key!r}") from e


def get_adapter_for_profile(profile_id: str) -> BoardAdapter:
    for ad in _ADAPTERS.values():
        if profile_id in ad.profiles:
            return ad
    raise KeyError(f"no adapter knows profile {profile_id!r}")


async def detect_all_boards() -> list[DetectedBoard]:
    """Run detection across every adapter. Returns the union, dedup'd by (port, profile)."""
    results = await asyncio.gather(*(ad.detect() for ad in _ADAPTERS.values()),
                                   return_exceptions=True)
    seen: set[tuple[str | None, str]] = set()
    out: list[DetectedBoard] = []
    for r in results:
        if isinstance(r, Exception):
            continue
        for b in r:
            key = (b.serial_port, b.profile_id)
            if key in seen:
                continue
            seen.add(key)
            out.append(b)
    return out
