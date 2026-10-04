"""A background job queue: submit a batch of price-recalculation jobs, poll for completion.

Written to exercise the newer static rules (blocking waits on a future/thread, unbounded polling loops,
unbounded module-level state) end to end, the same way the rest of OrderDesk's seeded defects exercise the
older ones. See corpus.yaml for which findings each one is expected to produce.
"""
from __future__ import annotations

import asyncio
import concurrent.futures
import time

_executor = concurrent.futures.ThreadPoolExecutor(max_workers=4)
# BUG: grown by create_job on every call, never popped or cleared anywhere in this file: a long-running
# process accumulates one entry per job forever (unbounded-module-container).
_jobs: dict[str, dict] = {}


def _recompute(order_id: int) -> dict:
    time.sleep(0.05)  # stands in for real recomputation work
    return {"order_id": order_id, "total": order_id * 1.0}


def create_job(order_id: int) -> str:
    job_id = f"job-{len(_jobs)}-{order_id}"
    _jobs[job_id] = {"status": "pending", "result": None}
    return job_id


async def submit_batch(order_ids: list[int]) -> list[str]:
    job_ids = [create_job(oid) for oid in order_ids]
    futures = [_executor.submit(_recompute, oid) for oid in order_ids]
    for future, jid in zip(futures, job_ids):
        # BUG: a ThreadPoolExecutor future's `.result()` blocks the calling thread until it's done; called
        # with no `await`/`to_thread` from an async def, it blocks the event loop itself for the whole
        # batch, one job at a time (blocking-in-async, via the future-wait rule).
        result = future.result()
        _jobs[jid] = {"status": "done", "result": result}
    return job_ids


async def get_job(job_id: str) -> dict:
    job = _jobs[job_id]
    # BUG: no deadline, no iteration cap: if the job id is stale or the batch task died before updating
    # it, this polls forever (unbounded-wait-loop).
    while job["status"] == "pending":
        await asyncio.sleep(0.05)
    return job
