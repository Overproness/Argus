import asyncio
from typing import Any

import httpx

from .. import settings


class InventoryClient:
    def __init__(self, base_url: str | None = None) -> None:
        self.base_url = base_url or settings.INVENTORY_URL
        self._client = httpx.AsyncClient(base_url=self.base_url, timeout=settings.INVENTORY_TIMEOUT)
        self._token: str | None = None
        self._token_lock = asyncio.Lock()

    async def _auth_header(self) -> dict[str, str]:
        async with self._token_lock:
            if self._token is None:
                resp = await self._client.post("/token")
                resp.raise_for_status()
                self._token = resp.json()["token"]
        return {"Authorization": f"Bearer {self._token}"}

    async def _request(self, method: str, path: str, **kwargs: Any) -> Any:
        for attempt in range(settings.INVENTORY_ATTEMPTS):
            try:
                headers = await self._auth_header()
                resp = await self._client.request(method, path, headers=headers, **kwargs)
                resp.raise_for_status()
                return resp.json()
            except httpx.HTTPError:
                if attempt == settings.INVENTORY_ATTEMPTS - 1:
                    raise
                await asyncio.sleep(0.2 * 2**attempt)

    async def stock_level(self, sku: str) -> int:
        data = await self._request("GET", f"/stock/{sku}")
        return int(data["available"])

    async def reserve(self, sku: str, qty: int) -> dict:
        return await self._request("POST", "/reserve", json={"sku": sku, "qty": qty})

    async def aclose(self) -> None:
        await self._client.aclose()
