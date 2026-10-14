import asyncio
import json
import urllib.request


class InventoryClient:
    def __init__(self, endpoint, timeout=0.2):
        self.endpoint = endpoint
        self.timeout = timeout

    def _read_inventory(self, sku):
        url = f"{self.endpoint}/inventory/{sku}"
        with urllib.request.urlopen(url, timeout=self.timeout) as response:
            return json.load(response)

    async def fetch_inventory(self, sku):
        return await asyncio.to_thread(self._read_inventory, sku)


class EventClient:
    def __init__(self, endpoint, timeout=0.08):
        self.endpoint = endpoint
        self.timeout = timeout

    def _read_event(self, event_id):
        url = f"{self.endpoint}/events/{event_id}"
        with urllib.request.urlopen(url, timeout=self.timeout) as response:
            return json.load(response)

    async def get_event(self, event_id):
        return await asyncio.to_thread(self._read_event, event_id)
