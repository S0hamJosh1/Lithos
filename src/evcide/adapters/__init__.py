"""Board adapter layer.

Per PDF Section 13: every supported board family implements the same interface.
Prevents STM32 / ESP32 / RP2040 / nRF logic from leaking across the system.
"""
from .base import BoardAdapter
from .registry import (
    detect_all_boards,
    get_adapter,
    get_adapter_for_profile,
    list_adapters,
)

__all__ = [
    "BoardAdapter",
    "get_adapter",
    "get_adapter_for_profile",
    "list_adapters",
    "detect_all_boards",
]
