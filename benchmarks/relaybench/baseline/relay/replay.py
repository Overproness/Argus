import asyncio
import urllib.error
import urllib.request


class ReplayClient:
    def __init__(self, endpoint):
        self.endpoint = endpoint

    def _attempt(self, payload):
        request = urllib.request.Request(self.endpoint + "/deliver", data=payload, method="POST")
        with urllib.request.urlopen(request, timeout=0.1) as response:
            return response.read()

    async def push(self, payload):
        for attempt in range(3):
            try:
                return await asyncio.to_thread(self._attempt, payload)
            except urllib.error.HTTPError:
                await asyncio.sleep(0.02)
        raise RuntimeError("Delivery unavailable")
