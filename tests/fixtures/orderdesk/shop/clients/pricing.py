import asyncio

import requests

from .. import settings


class PricingClient:
    def __init__(self, base_url: str | None = None) -> None:
        self.base_url = base_url or settings.PRICING_URL
        self.session = requests.Session()

    def price_for(self, sku: str) -> float:
        resp = self.session.get(f"{self.base_url}/price/{sku}")
        resp.raise_for_status()
        return float(resp.json()["price"])

    def quote(self, skus: list[str]) -> dict[str, float]:
        return {sku: self.price_for(sku) for sku in skus}

    def _price_bounded(self, sku: str) -> float:
        resp = requests.get(f"{self.base_url}/price/{sku}", timeout=3)
        resp.raise_for_status()
        return float(resp.json()["price"])

    async def price_for_async(self, sku: str) -> float:
        return await asyncio.to_thread(self._price_bounded, sku)
