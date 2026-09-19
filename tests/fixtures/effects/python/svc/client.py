import asyncio
import time

import requests
from tenacity import retry, stop_after_attempt


def fetch(url):
    return requests.get(url, timeout=30).json()


@retry(stop=stop_after_attempt(4))
def fetch_retrying(url):
    return fetch(url)


def sync_all(urls):
    for attempt in range(3):
        try:
            return [fetch_retrying(u) for u in urls]
        except requests.RequestException:
            continue
    return []


async def handler(url):
    return await asyncio.wait_for(asyncio.to_thread(lambda: fetch_retrying(url)), timeout=10)


async def blocking_handler(url):
    return fetch(url)


async def guarded(url):
    return await asyncio.wait_for(blocking_handler(url), timeout=5)


def poll_forever(url):
    while True:
        try:
            return fetch(url)
        except requests.RequestException:
            time.sleep(1)


def process_all(items):
    for item in items:
        try:
            fetch(item)
        except requests.RequestException:
            pass
