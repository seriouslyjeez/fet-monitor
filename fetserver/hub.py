"""In-process change notifications for the live dashboard (server-sent events)."""
from __future__ import annotations

import queue
import threading


class Hub:
    def __init__(self):
        self._subs = set()
        self._lock = threading.Lock()

    def subscribe(self) -> "queue.Queue":
        q = queue.Queue(maxsize=200)
        with self._lock:
            self._subs.add(q)
        return q

    def unsubscribe(self, q) -> None:
        with self._lock:
            self._subs.discard(q)

    def publish(self, message: dict) -> None:
        with self._lock:
            subs = list(self._subs)
        for q in subs:
            try:
                q.put_nowait(message)
            except queue.Full:
                pass        # a stalled browser just misses hints; it re-syncs on its next poll

    def subscriber_count(self) -> int:
        with self._lock:
            return len(self._subs)
