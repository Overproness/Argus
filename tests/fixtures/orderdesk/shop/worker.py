import asyncio
import logging
from typing import Awaitable, Callable

import httpx

from . import db, settings
from .services.orders import OrderService

log = logging.getLogger(__name__)


async def price_feed_loop(apply: Callable[[dict[str, float]], Awaitable[None]] = db.update_prices) -> None:
    async with httpx.AsyncClient(base_url=settings.PRICING_URL, timeout=5.0) as client:
        while True:
            try:
                resp = await client.get("/feed")
                resp.raise_for_status()
            except httpx.HTTPError:
                continue
            await apply(resp.json()["prices"])
            await asyncio.sleep(settings.PRICE_FEED_INTERVAL)


async def replay_pending(service: OrderService, pending: list[dict]) -> int:
    replayed = 0
    for order in pending:
        for attempt in range(3):
            try:
                await service.reserve_items(order["lines"])
                replayed += 1
                break
            except httpx.HTTPError:
                log.warning("replay failed for order %s (attempt %d)", order["id"], attempt + 1)
                await asyncio.sleep(1)
    return replayed
