"""Reproductions for findings in app.get_job (unbounded-wait-loop:65, blocking-in-async:61, io-in-loop:67)."""

import asyncio
import importlib

from auditor.repro.harness import evidence, call_with_deadline, loop_monitor


def _seed(m, job_id, status):
    m.DB.execute("insert or replace into jobs (id, name, status) values (?, ?, ?)", (job_id, "j", status))
    m.DB.commit()


def test_get_job_never_gives_up():
    m = importlib.import_module("app.main")
    _seed(m, 9001, "queued")  # no worker will ever flip it to "done"
    res = call_with_deadline(lambda: asyncio.run(m.get_job(9001)), 3)
    evidence(finding="unbounded-wait-loop@app.get_job:65", returned=res["returned"], deadline_s=3)
    # let the daemon thread's loop finish so it doesn't keep polling
    m.DB.execute("update jobs set status='done' where id=9001")
    m.DB.commit()
    assert not res["returned"]


def test_get_job_blocks_loop():
    m = importlib.import_module("app.main")
    _seed(m, 9002, "done")

    async def run():
        async with loop_monitor() as mon:
            for _ in range(200):
                await m.get_job(9002)
        return mon.max_lag

    lag = asyncio.run(run())
    evidence(finding="blocking-in-async@app.get_job:61", max_lag_s=round(lag, 5), calls=200)
    assert lag >= 0.05  # a real stall; in-memory sqlite point lookup is expected not to reach this


def test_get_job_io_in_loop_is_n_plus_one():
    m = importlib.import_module("app.main")
    _seed(m, 9003, "queued")
    real = m.DB
    seen = []

    class Proxy:
        def execute(self, sql, params=()):
            seen.append(params)
            return real.execute(sql, params)

        def __getattr__(self, n):
            return getattr(real, n)

    async def run():
        async def finish():
            await asyncio.sleep(0.3)
            real.execute("update jobs set status='done' where id=9003")
            real.commit()
        t = asyncio.create_task(finish())
        r = await m.get_job(9003)
        await t
        return r

    import pytest
    mp = pytest.MonkeyPatch()
    mp.setattr(m, "DB", Proxy())
    try:
        asyncio.run(run())
    finally:
        mp.undo()
    distinct = len(set(seen))
    evidence(finding="io-in-loop@app.get_job:67", queries=len(seen), distinct_param_sets=distinct)
    assert distinct > 1  # N+1 over a collection would query many different keys
