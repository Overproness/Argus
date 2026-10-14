import asyncio
import time


class HealthMonitor:
    def __init__(self, delay=0.005, deadline=0.02):
        self.delay = delay
        self.deadline = deadline

    async def pulse(self):
        await asyncio.sleep(self.delay)
        return "ready"

    async def check(self):
        return await asyncio.wait_for(self.pulse(), timeout=self.deadline)
