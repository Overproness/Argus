import threading
import time
from typing import Any, Awaitable, Callable


class TTLCache:
    def __init__(self, ttl: float) -> None:
        self.ttl = ttl
        self._data: dict[str, tuple[float, Any]] = {}
        self._lock = threading.Lock()

    async def get_or_load(self, key: str, loader: Callable[[], Awaitable[Any]]) -> Any:
        with self._lock:
            hit = self._data.get(key)
            if hit and hit[0] > time.monotonic():
                return hit[1]
            value = await loader()
            self._data[key] = (time.monotonic() + self.ttl, value)
            return value

    def invalidate(self, key: str) -> None:
        with self._lock:
            self._data.pop(key, None)
