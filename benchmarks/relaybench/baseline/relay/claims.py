import asyncio

NEXT_TICKET = 0
CLAIM_LOCK = asyncio.Lock()


async def next_ticket(authorize):
    global NEXT_TICKET
    async with CLAIM_LOCK:
        ticket = NEXT_TICKET
        await authorize()
        NEXT_TICKET = ticket + 1
        return ticket
