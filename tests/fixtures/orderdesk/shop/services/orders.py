import asyncio
from typing import Any

import httpx

from .. import db, settings
from ..clients.inventory import InventoryClient
from ..clients.pricing import PricingClient
from ..money import coupon_discount, order_total
from .events import EventBus


class OrderService:
    def __init__(self, pricing: PricingClient, inventory: InventoryClient, events: EventBus) -> None:
        self.pricing = pricing
        self.inventory = inventory
        self.events = events

    async def build_quote(self, skus: list[str]) -> dict[str, Any]:
        prices = self.pricing.quote(skus)
        lines = [{"sku": sku, "qty": 1, "unit_price": prices[sku]} for sku in skus]
        return {"lines": lines, "total": order_total(lines)}

    async def reserve_items(self, lines: list[dict]) -> list[dict]:
        reservations = []
        for line in lines:
            for attempt in range(3):
                try:
                    reservations.append(await self.inventory.reserve(line["sku"], line["qty"]))
                    break
                except httpx.HTTPError:
                    if attempt == 2:
                        raise
        return reservations

    async def _reserve_and_price(self, user_id: str, lines: list[dict], coupon: str | None) -> tuple[int, float]:
        prices = self.pricing.quote([l["sku"] for l in lines])
        await self.reserve_items(lines)
        priced = [{**l, "unit_price": prices[l["sku"]]} for l in lines]
        total = order_total(priced, coupon_discount(coupon))
        order_id = await db.create_order(user_id, total)
        await db.add_order_lines(order_id, priced)
        asyncio.create_task(self.events.deliver_now({"type": "order_created", "order_id": order_id}))
        return order_id, total

    async def checkout(self, user_id: str, lines: list[dict], coupon: str | None = None) -> tuple[int, float]:
        return await asyncio.wait_for(
            self._reserve_and_price(user_id, lines, coupon),
            timeout=settings.CHECKOUT_DEADLINE,
        )

    async def order_summary(self, order_id: int) -> list[dict]:
        lines = await db.get_order_lines(order_id)
        out = []
        for line in lines:
            product = await db.get_product(line["sku"])
            out.append(
                {
                    "sku": line["sku"],
                    "name": product["name"] if product else None,
                    "qty": line["qty"],
                    "unit_price": line["unit_price"],
                }
            )
        return out
