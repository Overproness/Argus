"""Small FastAPI app. Intentionally full of concurrency bugs and inefficiencies
for testing audit tooling. DO NOT use in production."""
import asyncio
import json
import sqlite3
import threading
import time

import requests
from fastapi import BackgroundTasks, FastAPI, HTTPException

app = FastAPI()

# ---- shared mutable global state, no locks ----
counter = 0
cache = {}
orders = []
balances = {"alice": 100, "bob": 100}
_db = sqlite3.connect("app.db", check_same_thread=False)  # one shared connection
_db.execute("CREATE TABLE IF NOT EXISTS items (id INTEGER PRIMARY KEY, name TEXT)")
_lock_a = threading.Lock()
_lock_b = threading.Lock()


# BUG: blocking call inside async handler blocks the whole event loop
@app.get("/slow")
async def slow():
    time.sleep(2)
    return {"ok": True}


# BUG: sync HTTP library in async def; sequential instead of gathered
@app.get("/aggregate")
async def aggregate():
    urls = [f"https://httpbin.org/delay/1?n={i}" for i in range(5)]
    results = []
    for u in urls:
        results.append(requests.get(u).json())
    return {"n": len(results)}


# BUG: read-modify-write race on global counter (with await between)
@app.post("/incr")
async def incr():
    global counter
    current = counter
    await asyncio.sleep(0)
    counter = current + 1
    return {"counter": counter}


# BUG: check-then-act race on cache; cache stampede; unbounded growth
@app.get("/expensive/{key}")
async def expensive(key: str):
    if key not in cache:
        await asyncio.sleep(1)  # simulated expensive work
        cache[key] = {"value": key * 1000, "ts": time.time()}
    return cache[key]


# BUG: non-atomic transfer, TOCTOU on balance, can go negative
@app.post("/transfer")
async def transfer(src: str, dst: str, amount: int):
    if balances[src] >= amount:
        await asyncio.sleep(0.01)
        balances[src] -= amount
        balances[dst] += amount
        return {"ok": True}
    raise HTTPException(400, "insufficient funds")


# BUG: N+1 queries, no index, shared connection across threads, SQL injection
@app.get("/items")
def list_items(name: str = ""):
    ids = _db.execute(f"SELECT id FROM items WHERE name LIKE '%{name}%'").fetchall()
    out = []
    for (i,) in ids:
        out.append(_db.execute(f"SELECT * FROM items WHERE id = {i}").fetchone())
    return out


# BUG: writes without commit/lock from multiple threads on shared connection
@app.post("/items")
def add_item(name: str):
    _db.execute("INSERT INTO items (name) VALUES (?)", (name,))
    return {"ok": True}


# BUG: inconsistent lock ordering -> deadlock
@app.post("/deadlock1")
def deadlock1():
    with _lock_a:
        time.sleep(0.1)
        with _lock_b:
            return {"ok": 1}


@app.post("/deadlock2")
def deadlock2():
    with _lock_b:
        time.sleep(0.1)
        with _lock_a:
            return {"ok": 2}


# BUG: threading.Lock held across await blocks loop; also never released on error
_async_lock = threading.Lock()


@app.post("/locked")
async def locked():
    _async_lock.acquire()
    await asyncio.sleep(1)
    if time.time() % 2 < 1:
        raise RuntimeError("boom")  # lock leaked
    _async_lock.release()
    return {"ok": True}


# BUG: fire-and-forget task, reference dropped, exceptions swallowed
async def _audit(msg: str):
    await asyncio.sleep(0.5)
    raise ValueError(msg)


@app.post("/audit")
async def audit(msg: str):
    asyncio.create_task(_audit(msg))
    return {"queued": True}


# BUG: background task does blocking CPU work + mutates list while iterating
def _process_orders():
    for o in orders:
        if o["status"] == "done":
            orders.remove(o)
        time.sleep(0.05)


@app.post("/orders")
async def create_order(item: str, bg: BackgroundTasks):
    orders.append({"item": item, "status": "done", "id": len(orders)})  # id race
    bg.add_task(_process_orders)
    return {"ok": True}


# BUG: CPU-bound work on the event loop
@app.get("/fib/{n}")
async def fib_endpoint(n: int):
    def fib(x):
        return x if x < 2 else fib(x - 1) + fib(x - 2)

    return {"result": fib(n)}


# BUG: re-reads and re-parses file on every request; sync file I/O in async;
# string concat in loop; unbounded response
@app.get("/report")
async def report():
    text = ""
    for _ in range(1000):
        with open("app/main.py") as f:
            text += json.dumps(f.read())
    return {"size": len(text)}


# BUG: thread started per request, never joined, no cap
@app.post("/spawn")
def spawn():
    t = threading.Thread(target=lambda: time.sleep(60))
    t.start()
    return {"ok": True}


# BUG: unbounded gather w/o semaphore, no timeout, no return_exceptions
@app.get("/fanout")
async def fanout(n: int = 10000):
    async def work(i):
        await asyncio.sleep(1)
        return i

    return {"sum": sum(await asyncio.gather(*[work(i) for i in range(n)]))}


# BUG: module-level startup does blocking network I/O; global set in startup w/o sync
@app.on_event("startup")
async def startup():
    global cache
    cache = requests.get("https://httpbin.org/json").json()
