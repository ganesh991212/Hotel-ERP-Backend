"""In-process realtime hub with a per-outlet monotonically increasing seq (docs/07).

Single-instance for v1. Multi-instance needs Redis pub/sub fan-out (BUILD-PLAN Phase 3);
the publish/subscribe interface stays the same.
"""

import asyncio
from collections import defaultdict
from typing import Any


class Hub:
    def __init__(self) -> None:
        self._subs: dict[str, set[asyncio.Queue]] = defaultdict(set)
        self._seq: dict[str, int] = defaultdict(int)

    def subscribe(self, channel: str) -> asyncio.Queue:
        q: asyncio.Queue = asyncio.Queue(maxsize=200)
        self._subs[channel].add(q)
        return q

    def unsubscribe(self, channel: str, q: asyncio.Queue) -> None:
        self._subs[channel].discard(q)

    def publish(self, channel: str, event: dict[str, Any]) -> None:
        self._seq[channel] += 1
        msg = {**event, "seq": self._seq[channel]}
        for q in list(self._subs[channel]):
            try:
                q.put_nowait(msg)
            except asyncio.QueueFull:
                # Slow consumer: drop; client detects the seq gap and refetches over REST.
                pass


hub = Hub()


def outlet_channel(outlet_id) -> str:
    return f"outlet:{outlet_id}"


def diner_channel(session_id) -> str:
    return f"diner:{session_id}"
