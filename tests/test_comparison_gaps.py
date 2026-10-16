"""Regression cases from the supplied main.py audit; this is development data."""
import importlib.util
import json
import shutil
import textwrap
from pathlib import Path

import pytest

from conftest import FIXTURES
from auditor import investigations, orchestrate, report
from auditor.analysis import RepoMap
from auditor.repro import runner, scaffold


TARGET = FIXTURES / "main_py_comparison"


def mapped(tmp_path, source=None):
    repo = tmp_path / "repo"
    repo.mkdir()
    if source is None:
        shutil.copytree(TARGET / "app", repo / "app")
    else:
        (repo / "example.py").write_text(textwrap.dedent(source), encoding="utf8")
    out = repo / ".audit"
    report.write(RepoMap(repo).load(), out)
    return repo, out


def test_small_module_shares_context_without_spending_fewer_budget_slots(tmp_path):
    repo, out = mapped(tmp_path)
    q = orchestrate.queue(out, {"total": 15, "per_round": 6})
    assert len(q["items"]) == 6 and len(q["batches"]) == 1
    remote = next(it for it in q["items"] if it["function"].endswith(".fetch_remote"))
    assert "wallclock-interval" in {f["rule"] for f in remote["findings"]}
    batch = q["batches"][0]
    assert batch["item_ids"] == [it["id"] for it in q["items"]]
    payload = json.loads(Path(batch["context_file"]).read_text(encoding="utf8"))
    assert payload["items"] == q["items"]
    assert all(it["repo"] == str(repo) and Path(it["abs_file"]).is_file() for it in payload["items"])
    guide = Path(batch["guide_file"]).read_text(encoding="utf8")
    assert "race-across-await" in guide and "Native probe" not in guide
    assert len(guide) < len(investigations.REFERENCE.read_text(encoding="utf8")) / 2
    ledger = json.loads((out / "verdicts.json").read_text(encoding="utf8"))
    assert len(ledger["rounds"][0]["items"]) == 6  # batching is not a budget bypass
    assert orchestrate.queue(out, dry_run=True)["budget"]["issued"] == 6


def _item(n, file="mod.py", lang="python", source="def f(): pass"):
    fid = f"io-without-timeout@mod.f{n}:1"
    return {"id": fid, "repo": "/repo", "abs_file": f"/repo/{file}", "lang": lang,
            "source": source, "findings": [{"id": fid, "rule": "io-without-timeout"}]}


def test_batches_keep_files_languages_and_size_limits_separate(tmp_path):
    items = [_item(n) for n in range(7)] + [_item(7, "other.py"), _item(8, lang="rust")]
    batches = investigations.prepare(items, tmp_path, 1)
    assert [b["functions"] for b in batches] == [6, 1, 1, 1]
    large = [_item(n, source="x" * 7000) for n in range(2)]
    assert len(investigations.prepare(large, tmp_path, 2)) == 2
    many = [_item(n) for n in range(2)]
    for item in many:
        item["findings"] *= 13
    assert len(investigations.prepare(many, tmp_path, 3)) == 2


def test_dry_run_does_not_write_context_or_scaffolds(tmp_path):
    _, out = mapped(tmp_path)
    result = orchestrate.queue(out, dry_run=True)
    assert result["batches"]
    assert not (out / "investigations").exists() and not (out / "repros").exists()


def test_unfilled_scaffold_is_skipped_and_an_edited_test_runs(tmp_path, monkeypatch):
    out = tmp_path / ".audit"
    item = _item(1)
    item["function"] = "example.work"
    path = scaffold.write(item, out / "repros")
    assert scaffold.is_unfilled(path)
    # No subprocess should execute the unfilled starter at all.
    with monkeypatch.context() as mp:
        mp.setattr(runner, "run_pytest", lambda *a: pytest.fail("unfilled scaffold executed"))
        result = runner.run_file(tmp_path, path, 30, "unused")
    assert result["tests"][0]["outcome"] == "skipped"
    assert runner.status_of(result, item["id"], str(path)) == "missing"
    path.write_text('from auditor.repro.harness import evidence\n'
                    'def test_real():\n'
                    f'    evidence(finding={item["id"]!r}, measured=2)\n'
                    '    assert 2 > 1\n', encoding="utf8")
    assert not scaffold.is_unfilled(path)
    result = runner.run(tmp_path, out, path)
    assert runner.status_of(result, item["id"], str(path)) == "passed"


def test_another_findings_passing_test_cannot_prove_this_one():
    result = {"tests": [{"file": "test_shared.py", "finding": "rule@one:1", "outcome": "passed"}]}
    assert runner.status_of(result, "rule@two:2", "test_shared.py") == "missing"
    assert runner.status_of(result, "rule@one:1", "test_shared.py") == "passed"
    result["tests"].append({"file": "test_shared.py", "finding": "rule@one:1", "outcome": "failed"})
    assert runner.status_of(result, "rule@one:1", "test_shared.py") == "failed"
    unlabelled = {"tests": [{"file": "test_empty.py", "finding": None, "outcome": "passed"}]}
    assert runner.status_of(unlabelled, "rule@one:1", "test_empty.py") == "missing"


def test_report_and_queue_recheck_a_confirmation_after_a_failed_rerun(tmp_path):
    _, out = mapped(tmp_path)
    q = orchestrate.queue(out, {"total": 15, "per_round": 1})
    fid = q["items"][0]["id"]
    repro = {"tests": [{"file": "test_shared.py", "finding": fid, "outcome": "passed",
                        "test": "test_real", "time_s": 0.01, "evidence": []}]}
    (out / "repro.json").write_text(json.dumps(repro), encoding="utf8")
    orchestrate.record(out, [{"finding": fid, "verdict": "confirmed", "repro_file": "test_shared.py"}])
    assert next(f for f in orchestrate.final(out)["findings"] if f["id"] == fid)["status"] == "proven"
    repro["tests"][0]["outcome"] = "failed"
    (out / "repro.json").write_text(json.dumps(repro), encoding="utf8")
    final = orchestrate.final(out)
    assert next(f for f in final["findings"] if f["id"] == fid)["status"] == "inconclusive"
    assert final["meta"]["investigated"] == 1 and final["meta"]["investigator_batches"] == 1
    retry = orchestrate.queue(out, {"per_round": 15}, dry_run=True)
    assert fid in {f["id"] for it in retry["items"] for f in it["findings"]}


@pytest.mark.parametrize("clock", ["time.time", "wall.time", "epoch"])
def test_relative_expiry_clock_aliases_are_detected(tmp_path, clock):
    _, out = mapped(tmp_path, f'''
        import time
        import time as wall
        from time import time as epoch
        CACHE = {{}}
        def lookup(key, ttl=60):
            cached = CACHE.get(key)
            if cached and cached[0] > {clock}():
                return cached[1]
            CACHE[key] = ({clock}() + ttl, "value")
    ''')
    findings = json.loads((out / "map.json").read_text(encoding="utf8"))["findings"]
    assert any(f["rule"] == "wallclock-interval" for f in findings)


def test_monotonic_expiry_and_ordinary_timestamps_are_quiet(tmp_path):
    _, out = mapped(tmp_path, '''
        import time
        CACHE = {}
        def lookup(key):
            cached = CACHE.get(key)
            if cached and cached[0] > time.monotonic():
                return cached[1]
            CACHE[key] = (time.monotonic() + 60, "value")
        def timestamp():
            return {"created": time.time()}
        def shadow(time):
            if time.time() > 0:
                return time.time() + 10
    ''')
    findings = json.loads((out / "map.json").read_text(encoding="utf8"))["findings"]
    assert not any(f["rule"] == "wallclock-interval" for f in findings)


def test_actual_cache_clock_jump_changes_behavior_on_fixed_source(tmp_path, monkeypatch):
    import asyncio
    import types

    httpx = pytest.importorskip("httpx")
    pytest.importorskip("fastapi")
    original_init = httpx.AsyncClient.__init__
    requests = []

    async def peer(request):
        requests.append(str(request.url))
        return httpx.Response(200, text="local peer")

    def client_init(self, *a, **kw):
        kw["transport"] = httpx.MockTransport(peer)
        original_init(self, *a, **kw)

    monkeypatch.setattr(httpx.AsyncClient, "__init__", client_init)
    source = (TARGET / "app/main.py").read_text(encoding="utf8")
    effects = []
    for fixed in (False, True):
        code = source
        if fixed:
            code = code.replace("cached[0] > time.time()", "cached[0] > time.monotonic()")
            code = code.replace("(time.time() + 60, value)", "(time.monotonic() + 60, value)")
        path = tmp_path / f"target_{fixed}.py"
        path.write_text(code, encoding="utf8")
        spec = importlib.util.spec_from_file_location(f"comparison_{fixed}", path)
        target = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(target)
        clocks = {"wall": 1000, "elapsed": 0}
        target.time = types.SimpleNamespace(time=lambda: clocks["wall"], monotonic=lambda: clocks["elapsed"])
        try:
            async def scenario():
                before = len(requests)
                await target.fetch_remote("key")
                clocks.update(wall=-2539, elapsed=61)  # one minute elapsed, clock moved backward one hour
                result = await target.fetch_remote("key")
                return result["cached"], len(requests) - before

            effects.append(asyncio.run(scenario()))
        finally:
            target.DB.close()
            target.POOL.shutdown(wait=True)
    assert effects == [(True, 1), (False, 2)]  # same trigger, actual buggy and fixed code


def test_additional_resource_leads_are_in_the_real_target(tmp_path):
    _, out = mapped(tmp_path)
    findings = json.loads((out / "map.json").read_text(encoding="utf8"))["findings"]
    assert ("app.fetch_remote", "buffered-response-slice") in {(f["function"], f["rule"]) for f in findings}
    assert ("app.create_job", "unbounded-background-tasks") in {(f["function"], f["rule"]) for f in findings}


def test_resource_rules_require_known_http_types_and_detached_request_tasks(tmp_path):
    _, out = mapped(tmp_path, '''
        import asyncio
        import httpx
        from httpx import AsyncClient as PeerClient
        from fastapi import FastAPI
        app = FastAPI()
        async def bad():
            async with PeerClient() as peer:
                response = await peer.get("http://127.0.0.1/")
            return response.content[:100]
        async def streamed():
            async with httpx.AsyncClient() as peer:
                async with peer.stream("GET", "http://127.0.0.1/") as response:
                    async for chunk in response.aiter_bytes(chunk_size=100):
                        return chunk
        def custom_store(store):
            response = store.get("key")
            return response.text[:100]
        @app.on_event("startup")
        async def startup():
            asyncio.create_task(work())
        @app.post("/joined")
        async def joined():
            await asyncio.create_task(work())
        async def helper():
            asyncio.create_task(work())
    ''')
    findings = json.loads((out / "map.json").read_text(encoding="utf8"))["findings"]
    got = {(f["function"], f["rule"]) for f in findings}
    assert ("example.bad", "buffered-response-slice") in got
    assert not any(fn in {"example.streamed", "example.custom_store"} and rule == "buffered-response-slice"
                   for fn, rule in got)
    assert not any(rule == "unbounded-background-tasks" for _, rule in got)


def _load_app(path, source):
    path.write_text(source, encoding="utf8")
    spec = importlib.util.spec_from_file_location(path.stem, path)
    target = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(target)
    return target


def test_actual_response_slice_downloads_the_full_body_but_streamed_fix_stops(tmp_path, monkeypatch):
    import asyncio

    httpx = pytest.importorskip("httpx")
    pytest.importorskip("fastapi")
    measured = {"bytes": 0}

    class Body(httpx.AsyncByteStream):
        async def __aiter__(self):
            for _ in range(256):
                measured["bytes"] += 4096
                yield b"x" * 4096

    async def peer(request):
        return httpx.Response(200, stream=Body())

    original_init = httpx.AsyncClient.__init__

    def client_init(self, *a, **kw):
        kw["transport"] = httpx.MockTransport(peer)
        original_init(self, *a, **kw)

    monkeypatch.setattr(httpx.AsyncClient, "__init__", client_init)
    source = (TARGET / "app/main.py").read_text(encoding="utf8")
    old = ('    async with httpx.AsyncClient() as client:\n'
           '        response = await client.get(f"https://example.com/?key={key}", timeout=10)\n'
           '    value = response.text[:100]')
    new = ('    async with httpx.AsyncClient() as client:\n'
           '        async with client.stream("GET", f"https://example.com/?key={key}", timeout=10) as response:\n'
           '            collected = bytearray()\n'
           '            async for chunk in response.aiter_bytes(chunk_size=100):\n'
           '                collected.extend(chunk[:100 - len(collected)])\n'
           '                if len(collected) >= 100:\n'
           '                    break\n'
           '    value = bytes(collected).decode("utf8")')
    assert source.count(old) == 1
    effects = []
    for fixed in (False, True):
        target = _load_app(tmp_path / f"buffered_{fixed}.py", source.replace(old, new) if fixed else source)
        measured["bytes"] = 0
        try:
            result = asyncio.run(target.fetch_remote("key"))
            effects.append((len(result["value"]), measured["bytes"]))
        finally:
            target.DB.close()
            target.POOL.shutdown(wait=True)
    assert effects == [(100, 1048576), (100, 4096)]


def test_actual_detached_jobs_accumulate_and_awaited_control_does_not(tmp_path):
    import asyncio
    import time
    import types

    pytest.importorskip("httpx")
    pytest.importorskip("fastapi")
    source = (TARGET / "app/main.py").read_text(encoding="utf8")
    old = "asyncio.create_task(process_job(job_id, name))"
    assert source.count(old) == 1
    effects = []
    for fixed in (False, True):
        code = source.replace(old, "await process_job(job_id, name)") if fixed else source
        target = _load_app(tmp_path / f"background_{fixed}.py", code)
        # Blocking sleep is unrelated to admission. Real tasks, coroutine yields,
        # route code and SQLite writes still execute; no production services run.
        target.time = types.SimpleNamespace(time=time.time, sleep=lambda _: None)
        try:
            async def scenario():
                before = asyncio.all_tasks()
                for n in range(20):
                    await target.create_job(f"job-{n}")
                pending = asyncio.all_tasks() - before
                count = len(pending)
                for task in pending:
                    task.cancel()
                await asyncio.gather(*pending, return_exceptions=True)
                return count

            effects.append(asyncio.run(scenario()))
        finally:
            target.DB.close()
            target.POOL.shutdown(wait=True)
    assert effects == [20, 0]
