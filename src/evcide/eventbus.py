"""In-process pub/sub event bus for live streaming to the frontend.

The verify engine publishes RuntimeEvents and the final VerificationResult to a
topic (a client-supplied correlation id == stream id). The WebSocket endpoint
subscribes to that topic and forwards every message to Void IDE.

Stdlib asyncio only — no broker, no extra dependency. Each topic keeps a bounded
replay buffer so a subscriber that connects slightly *after* publishing begins
still receives the earlier messages instead of racing and dropping them. That
matters because the frontend opens the WS and triggers /verify as two separate
calls; we must not depend on their exact ordering.
"""
from __future__ import annotations

import asyncio
from collections import defaultdict, deque
from typing import Any, AsyncIterator, Deque

_SENTINEL = object()


class EventBus:
    def __init__(self, replay_size: int = 500) -> None:
        self._subs: dict[str, set[asyncio.Queue]] = defaultdict(set)
        self._replay: dict[str, Deque[dict[str, Any]]] = {}
        self._closed: set[str] = set()
        self._replay_size = replay_size

    async def publish(self, topic: str, message: dict[str, Any]) -> None:
        buf = self._replay.get(topic)
        if buf is None:
            buf = self._replay[topic] = deque(maxlen=self._replay_size)
        buf.append(message)
        for q in list(self._subs.get(topic, ())):
            q.put_nowait(message)

    def close(self, topic: str) -> None:
        """Signal end-of-stream to current and future subscribers of `topic`."""
        self._closed.add(topic)
        for q in list(self._subs.get(topic, ())):
            q.put_nowait(_SENTINEL)

    async def subscribe(self, topic: str) -> AsyncIterator[dict[str, Any]]:
        """Yield replayed-then-live messages until the topic is closed."""
        q: asyncio.Queue = asyncio.Queue()
        self._subs[topic].add(q)
        try:
            for msg in list(self._replay.get(topic, ())):
                yield msg
            if topic in self._closed:
                return
            while True:
                msg = await q.get()
                if msg is _SENTINEL:
                    return
                yield msg
        finally:
            self._subs[topic].discard(q)
            if not self._subs[topic]:
                self._subs.pop(topic, None)

    def reset(self, topic: str) -> None:
        """Drop replay buffer + closed-state for a topic (e.g. reusing an id)."""
        self._replay.pop(topic, None)
        self._closed.discard(topic)


#: Process-wide singleton used by the API layer.
bus = EventBus()
