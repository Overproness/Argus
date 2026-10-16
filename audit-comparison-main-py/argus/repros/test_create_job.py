"""Reproduction for create_job: unbounded WORK_QUEUE and fire-and-forget task."""

import asyncio
import gc
import importlib

from auditor.repro.harness import evidence


def test_create_job_grows_without_bound(monkeypatch):
    m = importlib.import_module("app.main")
    monkeypatch.setattr(m, "WORK_QUEUE", [])
    N = 50

    async def run():
        for i in range(N):
            await m.create_job(f"job-{i}")
        # let the background tasks finish so nothing is left pending
        await asyncio.sleep(0.01 + 0.2 * N + 0.5)

    size_before = len(m.WORK_QUEUE)
    asyncio.run(run())
    size_after = len(m.WORK_QUEUE)
    evidence(finding="unbounded-module-container@app.create_job:45", before=size_before, after=size_after, calls=N)
    assert size_after == size_before + N  # nothing was ever evicted


class _FailingUpdateDB:
    """Proxy for the module's sqlite connection: the UPDATE in process_job fails."""

    def __init__(self, real):
        self._real = real

    def execute(self, sql, *a):
        if sql.lstrip().lower().startswith("update"):
            raise RuntimeError("simulated db failure in background job")
        return self._real.execute(sql, *a)

    def commit(self):
        return self._real.commit()


def test_create_job_task_exception_is_lost(monkeypatch):
    m = importlib.import_module("app.main")
    monkeypatch.setattr(m, "WORK_QUEUE", [])
    monkeypatch.setattr(m, "DB", _FailingUpdateDB(m.DB))
    seen = {"handler": [], "caller_exc": None, "response": None}

    async def run():
        loop = asyncio.get_running_loop()
        loop.set_exception_handler(lambda l, ctx: seen["handler"].append(ctx))
        try:
            seen["response"] = await m.create_job("x")
        except Exception as e:  # the caller would see this if the task were awaited
            seen["caller_exc"] = e
        await asyncio.sleep(0.5)  # task fails in the background
        gc.collect()
        await asyncio.sleep(0.05)

    asyncio.run(run())
    gc.collect()
    msgs = [c.get("message", "") for c in seen["handler"]]
    evidence(
        finding="fire-and-forget-task@app.create_job:46",
        response=seen["response"],
        caller_exception=repr(seen["caller_exc"]),
        loop_handler_messages=msgs,
    )
    # caller got a normal "queued" response; the failure reached only the loop's
    # "Task exception was never retrieved" handler
    assert seen["caller_exc"] is None
    assert seen["response"] is not None and seen["response"]["status"] == "queued"
    assert any("never retrieved" in s for s in msgs)
