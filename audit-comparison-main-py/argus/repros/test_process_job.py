"""Reproduction for unbounded-module-container@app.process_job:55."""

import asyncio
import importlib

from auditor.repro.harness import evidence


def test_process_job_grows_without_bound(monkeypatch):
    m = importlib.import_module("app.main")
    monkeypatch.setattr(m, "EVENTS", [])
    monkeypatch.setattr(m.time, "sleep", lambda s: None)  # skip the 0.2 s blocking sleep; irrelevant to growth
    N = 50

    async def run():
        for i in range(N):
            await m.process_job(i, f"job-{i}")

    size_before = len(m.EVENTS)
    asyncio.run(run())
    size_after = len(m.EVENTS)
    evidence(finding="unbounded-module-container@app.process_job:55", before=size_before, after=size_after, calls=N)
    assert size_after == size_before + N  # nothing was ever evicted
