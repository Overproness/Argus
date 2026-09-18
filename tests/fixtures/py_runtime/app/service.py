import asyncio
import time
import urllib.request

BASE = "http://127.0.0.1:0"


def fetch_quote(symbol):
    return urllib.request.urlopen(f"{BASE}/quote/{symbol}").read()


def fetch_quote_bounded(symbol):
    return urllib.request.urlopen(f"{BASE}/quote/{symbol}", timeout=3).read()


def compute(symbol):
    return len(fetch_quote(symbol))


def matrix(rows):
    return [[a * b for b in rows] for a in rows]


def depth(n):
    return 0 if n == 0 else 1 + depth(n - 1)


async def tick(symbols):
    for s in symbols:
        compute(s)
    await asyncio.to_thread(fetch_quote_bounded, "ETH")
    time.sleep(0.12)
    await asyncio.sleep(0)


async def warm(symbols):
    for s in symbols:
        await asyncio.to_thread(fetch_quote, s)
