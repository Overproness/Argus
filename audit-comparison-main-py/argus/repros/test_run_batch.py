"""Reproduction for blocking-in-async@app.run_batch:89 (hang-reaches-entry@app.run_batch:86 is rejected by reading:
the pooled callable is json.dumps on an in-memory dict, no I/O, so nothing can wait forever)."""

import asyncio
import threading
import time

from auditor.repro.harness import evidence, loop_monitor


def _lag_for(n):
    import app.main as main

    items = [f"item-{i}" for i in range(n)]

    async def run():
        async with loop_monitor() as m:
            await asyncio.sleep(0.05)
            t0 = time.perf_counter()
            res = await main.run_batch(items)
            elapsed = time.perf_counter() - t0
            await asyncio.sleep(0.05)
        return m.max_lag, elapsed, res

    return asyncio.run(run())


def test_run_batch_blocks_the_loop(monkeypatch):
    import app.main as main

    # Is the wait on the loop thread? Record the thread that runs future.result via a probe on the pool callable.
    seen = {}
    orig_submit = main.POOL.submit

    def spy_submit(fn, *a, **k):
        seen.setdefault("worker_threads", set())
        def wrapped(*aa, **kk):
            seen["worker_threads"].add(threading.current_thread().name)
            return fn(*aa, **kk)
        return orig_submit(wrapped, *a, **k)

    monkeypatch.setattr(main.POOL, "submit", spy_submit)
    small = _lag_for(10)
    big = _lag_for(200_000)
    evidence(
        finding="blocking-in-async@app.run_batch:89",
        lag_n10_s=small[0], elapsed_n10_s=small[1],
        lag_n200k_s=big[0], elapsed_n200k_s=big[1],
        submitted=big[2],
    )
    # the loop is held for the whole batch: lag tracks the batch's wall time, and grows with n
    assert big[0] >= 0.8 * big[1] and big[0] > 10 * max(small[0], 1e-4)
