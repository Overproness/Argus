import asyncio
import logging
from typing import Any

import httpx

from .. import settings

log = logging.getLogger(__name__)


class EventBus:
    def __init__(self, sink_url: str | None = None) -> None:
        self.sink_url = sink_url or settings.EVENTS_URL
        self.queue: asyncio.Queue[dict[str, Any]] = asyncio.Queue()

    def publish(self, event: dict[str, Any]) -> None:
        self.queue.put_nowait(event)

    async def deliver_now(self, event: dict[str, Any]) -> None:
        async with httpx.AsyncClient(timeout=5.0) as client:
            resp = await client.post(f"{self.sink_url}/events", json=event)
            resp.raise_for_status()

    async def run(self) -> None:
        while True:
            event = await self.queue.get()
            try:
                await self.deliver_now(event)
            except httpx.HTTPError:
                log.warning("event delivery failed: %s", event.get("type"))
            finally:
                self.queue.task_done()


class AuditLog:
    def __init__(self) -> None:
        self.queue: asyncio.Queue[str] = asyncio.Queue(maxsize=1000)
        self.lines: list[str] = []

    async def record(self, line: str) -> None:
        await self.queue.put(line)

    async def run(self) -> None:
        while True:
            line = await self.queue.get()
            self.lines = (self.lines + [line])[-500:]
            self.queue.task_done()
