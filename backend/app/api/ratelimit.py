"""Tiny in-memory sliding-window limiter (per process). Enough to blunt password guessing and message floods in a single-process
deployment; behind several workers use a shared store (see README known limits). The clock is injectable for tests."""

import time
from collections import defaultdict, deque
from collections.abc import Callable


class SlidingWindow:
    def __init__(self, max_events: int, window_seconds: float, clock: Callable[[], float] = time.monotonic) -> None:
        self.max, self.window, self._clock = max_events, window_seconds, clock
        self._events: dict[str, deque[float]] = defaultdict(deque)

    def _prune(self, key: str) -> deque[float]:
        q, cutoff = self._events[key], self._clock() - self.window
        while q and q[0] <= cutoff:
            q.popleft()
        if not q:
            self._events.pop(key, None)
            return deque()
        return q

    def blocked(self, key: str) -> bool:
        return len(self._prune(key)) >= self.max

    def record(self, key: str) -> None:
        self._prune(key)
        self._events[key].append(self._clock())

    def allow(self, key: str) -> bool:
        """Record one event and say whether it is within the limit."""
        if self.blocked(key):
            return False
        self.record(key)
        return True

    def reset(self, key: str) -> None:
        self._events.pop(key, None)
