import asyncio
import random

import httpx

from .. import settings


async def fetch_reviews(sku: str) -> list[dict]:
    async with httpx.AsyncClient(base_url=settings.REVIEWS_URL, timeout=2.0) as client:
        for attempt in range(4):
            try:
                resp = await client.get(f"/reviews/{sku}")
                resp.raise_for_status()
                return resp.json()["reviews"]
            except httpx.HTTPError:
                if attempt == 3:
                    return []
                await asyncio.sleep(min(2.0, 0.1 * 2**attempt) + random.uniform(0, 0.1))
    return []
