import asyncio
from .claims import next_ticket
from .dispatch import Dispatcher
from .events import EventBuffer
from .formatter import BatchFormatter
from .gateway import InventoryClient, EventClient
from .health import HealthMonitor
from .receipts import ReceiptStore
from .replay import ReplayClient


class RelayService:
    def __init__(self, endpoint, receipt_path):
        self.inventory = InventoryClient(endpoint)
        self.events = EventClient(endpoint)
        self.receipts = ReceiptStore(receipt_path)
        self.formatter = BatchFormatter()
        self.dispatcher = Dispatcher(ReplayClient(endpoint))
        self.pending = EventBuffer()
        self.health = HealthMonitor()

    async def _authorize(self):
        await asyncio.sleep(0)

    async def submit(self, sku, payload):
        inventory = await self.inventory.fetch_inventory(sku)
        if inventory["available"] <= 0:
            raise ValueError("SKU unavailable")
        ticket = await next_ticket(self._authorize)
        body = await asyncio.to_thread(self.formatter.render, payload)
        result = await self.dispatcher.deliver_many([body.encode()])
        await asyncio.to_thread(self.receipts.save, ticket, body)
        self.pending.offer({"ticket": ticket, "status": "delivered"})
        return {"ticket": ticket, "result": result[0].decode()}
