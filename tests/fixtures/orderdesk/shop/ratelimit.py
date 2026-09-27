import asyncio
import time


class Throttle:
    """Spaces calls at least 1/per_second apart."""

    def __init__(self, per_second: float) -> None:
        self.min_interval = 1.0 / per_second
        self._last = 0.0

    def wait(self) -> None:
        now = time.time()
        delay = self._last + self.min_interval - now
        if delay > 0:
            time.sleep(delay)
        self._last = time.time()


class TokenBucket:
    def __init__(self, rate: float, capacity: int) -> None:
        self.rate = rate
        self.capacity = capacity
        self._tokens = float(capacity)
        self._updated = time.monotonic()
        self._lock = asyncio.Lock()

    async def acquire(self) -> None:
        async with self._lock:
            while True:
                now = time.monotonic()
                self._tokens = min(self.capacity, self._tokens + (now - self._updated) * self.rate)
                self._updated = now
                if self._tokens >= 1:
                    self._tokens -= 1
                    return
                await asyncio.sleep((1 - self._tokens) / self.rate)
