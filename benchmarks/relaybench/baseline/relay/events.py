import asyncio


class EventBuffer:
    def __init__(self, capacity=16):
        self.queue = asyncio.Queue(maxsize=capacity)

    def offer(self, event):
        try:
            self.queue.put_nowait(event)
            return True
        except asyncio.QueueFull:
            return False

    async def take(self):
        return await self.queue.get()
