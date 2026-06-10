"""Board adapter layer.

Per PDF Section 13: every supported board family implements the same interface.
Prevents STM32 / ESP32 / RP2040 / nRF logic from leaking across the system.
"""
from .base import BoardAdapter
from .registry import get_adapter, list_adapters, detect_all_boards

__all__ = ["BoardAdapter", "get_adapter", "list_adapters", "detect_all_boards"]
