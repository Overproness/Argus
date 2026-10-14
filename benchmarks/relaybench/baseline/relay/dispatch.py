import asyncio


class Dispatcher:
    def __init__(self, adapter, parallelism=4):
        self.adapter = adapter
        self.parallelism = parallelism

    async def _deliver(self, delivery):
        return await self.adapter.push(delivery)

    async def deliver_many(self, deliveries):
        semaphore = asyncio.Semaphore(self.parallelism)

        async def limited(delivery):
            async with semaphore:
                return await self._deliver(delivery)

        return await asyncio.gather(*[limited(delivery) for delivery in deliveries])
