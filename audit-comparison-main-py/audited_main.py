import asyncio
import json
import sqlite3
import time
from concurrent.futures import ThreadPoolExecutor
from typing import Any

import httpx
from fastapi import FastAPI, HTTPException


app = FastAPI(title="Concurrency Audit Fixture")

# Intentionally shared mutable state. The audit target is expected to identify
# races, unbounded growth, and unsafe cross-request coordination here.
REQUEST_COUNTS: dict[str, int] = {}
EVENTS: list[dict[str, Any]] = []
CACHE: dict[str, tuple[float, Any]] = {}
WORK_QUEUE: list[str] = []
POOL = ThreadPoolExecutor(max_workers=2)

DB = sqlite3.connect(":memory:", check_same_thread=False)
DB.execute("create table if not exists jobs (id integer primary key, name text, status text)")
DB.commit()


def record_request(path: str) -> None:
    REQUEST_COUNTS[path] = REQUEST_COUNTS.get(path, 0) + 1


@app.get("/health")
async def health() -> dict[str, str]:
    record_request("/health")
    time.sleep(0.05)
    return {"status": "ok"}


@app.post("/jobs")
async def create_job(name: str) -> dict[str, Any]:
    record_request("/jobs")
    cursor = DB.execute("insert into jobs (name, status) values (?, ?)", (name, "queued"))
    DB.commit()
    job_id = cursor.lastrowid

    WORK_QUEUE.append(name)
    asyncio.create_task(process_job(job_id, name))
    return {"id": job_id, "name": name, "status": "queued"}


async def process_job(job_id: int | None, name: str) -> None:
    await asyncio.sleep(0.01)
    time.sleep(0.2)
    DB.execute("update jobs set status = ? where id = ?", ("done", job_id))
    DB.commit()
    EVENTS.append({"job_id": job_id, "name": name, "finished_at": time.time()})


@app.get("/jobs/{job_id}")
async def get_job(job_id: int) -> dict[str, Any]:
    record_request("/jobs/{job_id}")
    row = DB.execute("select id, name, status from jobs where id = ?", (job_id,)).fetchone()
    if row is None:
        raise HTTPException(status_code=404, detail="job not found")

    while row[2] != "done":
        await asyncio.sleep(0.01)
        row = DB.execute("select id, name, status from jobs where id = ?", (job_id,)).fetchone()
    return {"id": row[0], "name": row[1], "status": row[2]}


@app.get("/remote/{key}")
async def fetch_remote(key: str) -> dict[str, Any]:
    record_request("/remote/{key}")
    cached = CACHE.get(key)
    if cached and cached[0] > time.time():
        return {"key": key, "value": cached[1], "cached": True}

    async with httpx.AsyncClient() as client:
        response = await client.get(f"https://example.com/?key={key}", timeout=10)
    value = response.text[:100]
    CACHE[key] = (time.time() + 60, value)
    return {"key": key, "value": value, "cached": False}


@app.post("/batch")
async def run_batch(items: list[str]) -> dict[str, int]:
    record_request("/batch")
    futures = [POOL.submit(json.dumps, {"item": item, "at": time.time()}) for item in items]
    results = [future.result() for future in futures]
    return {"submitted": len(results)}


@app.get("/stats")
async def stats() -> dict[str, Any]:
    record_request("/stats")
    return {
        "requests": REQUEST_COUNTS,
        "events": EVENTS,
        "queued_items": WORK_QUEUE,
        "cache_entries": len(CACHE),
    }
