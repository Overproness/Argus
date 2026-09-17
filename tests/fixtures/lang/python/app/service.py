import asyncio
import threading
import time

import requests
from aiohttp import ClientSession

lock = threading.Lock()


def fetch_quote(symbol):
    return requests.get(f"https://x/quote/{symbol}").json()


def fetch_quote_bounded(symbol):
    return requests.get(f"https://x/quote/{symbol}", timeout=3).json()


def compute(symbol):
    return fetch_quote(symbol) * 2


async def tick(symbols):
    for s in symbols:
        compute(s)
    await asyncio.to_thread(fetch_quote, "ETH")
    loop = asyncio.get_running_loop()
    await loop.run_in_executor(None, lambda: fetch_quote("SOL"))
    time.sleep(1)
    with lock:
        await asyncio.sleep(0)


async def stream(session: ClientSession, symbols):
    for s in symbols:
        await session.get(f"https://x/{s}")


def matrix(rows):
    return [[a * b for b in rows] for a in rows]
