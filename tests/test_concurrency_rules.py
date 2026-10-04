"""Recall on a FastAPI app with 16 planted concurrency and reliability bugs, and the rules that find them."""
import shutil
import sys
import textwrap

import pytest

from conftest import FIXTURES

from auditor import paths
from auditor.analysis import RepoMap

FIX = FIXTURES / "py_concurrency"

# Every planted bug, by handler: (function, rule) pairs the map must report.
PLANTED = {
    ("app.slow", "blocking-in-async"),
    ("app.aggregate", "blocking-in-async"), ("app.aggregate", "io-without-timeout"), ("app.aggregate", "io-in-loop"),
    ("app.incr", "race-across-await"),
    ("app.expensive", "race-across-await"),
    ("app.transfer", "race-across-await"),
    ("app.list_items", "io-in-loop"), ("app.list_items", "sql-injection"),
    ("app.add_item", "uncommitted-write"),
    ("app.deadlock1", "lock-order-inversion"),
    ("app.locked", "lock-across-await"), ("app.locked", "lock-not-released"),
    ("app.audit", "fire-and-forget-task"),
    ("app._process_orders", "mutate-while-iterating"),
    ("app.fib_endpoint", "cpu-heavy-in-async"),
    ("app.report", "blocking-in-async"),
    ("app.spawn", "unbounded-threads"),
    ("app.fanout", "unbounded-concurrency"),
    ("app.startup", "io-without-timeout"),
}


def _findings(root):
    return {(f.function, f.rule): f for f in RepoMap(root).load().findings}


def test_every_planted_bug_has_a_lead():
    got = _findings(FIX)
    assert not PLANTED - set(got), sorted(PLANTED - set(got))
    assert all(got[k].severity in ("high", "medium") for k in PLANTED), "each is worth an investigation"


def test_known_non_issues_are_not_alarms():
    got = _findings(FIX)
    # sqlite3 waits at most its 5 s busy timeout: not a hang.
    assert ("app.list_items", "hang-reaches-entry") not in got and ("app.add_item", "hang-reaches-entry") not in got
    # A startup hook runs before traffic: blocking there delays boot, it does not freeze requests.
    assert got[("app.startup", "blocking-in-async")].severity == "low"


def _map(tmp_path, src):
    (tmp_path / "m.py").write_text(textwrap.dedent(src))
    return {(f.function, f.rule) for f in RepoMap(tmp_path).load().findings}


def test_race_needs_shared_state_and_an_await_between(tmp_path):
    got = _map(tmp_path, """
        import asyncio
        cache = {}
        async def locked(k):
            async with LOCK:
                if k not in cache:
                    await asyncio.sleep(0)
                    cache[k] = 1
        async def local_only(k):
            cache = {}
            if k not in cache:
                await asyncio.sleep(0)
                cache[k] = 1
        async def no_await(k):
            if k not in cache:
                cache[k] = 1
        async def racy(k):
            if k not in cache:
                await asyncio.sleep(0)
                cache[k] = 1
        """)
    races = {fn for fn, rule in got if rule == "race-across-await"}
    assert races == {"m.racy"}


def test_lock_order_through_a_call(tmp_path):
    got = _map(tmp_path, """
        import threading
        a = threading.Lock()
        b = threading.Lock()
        def take_b():
            with b:
                pass
        def one():
            with a:
                take_b()
        def two():
            with b:
                with a:
                    pass
        def same_order():
            with a:
                with b:
                    pass
        """)
    assert ("m.one", "lock-order-inversion") in got


def test_released_in_finally_is_fine(tmp_path):
    got = _map(tmp_path, """
        import threading
        lock = threading.Lock()
        def good():
            lock.acquire()
            try:
                work()
            finally:
                lock.release()
        def bad():
            lock.acquire()
            work()
            lock.release()
        """)
    assert ("m.bad", "lock-not-released") in got and ("m.good", "lock-not-released") not in got


def test_mutation_of_a_copy_is_fine(tmp_path):
    got = _map(tmp_path, """
        def bad(xs):
            for x in xs:
                xs.remove(x)
        def good(xs):
            for x in list(xs):
                xs.remove(x)
        """)
    assert ("m.bad", "mutate-while-iterating") in got and ("m.good", "mutate-while-iterating") not in got


def test_bounded_fanout_and_joined_threads_are_fine(tmp_path):
    got = _map(tmp_path, """
        import asyncio, threading
        async def capped(items):
            sem = asyncio.Semaphore(10)
            return await asyncio.gather(*[one(sem, i) for i in items])
        def joined():
            t = threading.Thread(target=print)
            t.start()
            t.join()
        """)
    assert not {r for _, r in got} & {"unbounded-concurrency", "unbounded-threads"}


def test_blocking_future_result_and_thread_join_in_async(tmp_path):
    got = _map(tmp_path, """
        import threading, concurrent.futures

        async def batch(items):
            executor = concurrent.futures.ThreadPoolExecutor()
            future = executor.submit(work, items)
            return future.result()

        async def spawn_and_wait():
            t = threading.Thread(target=work)
            t.start()
            t.join()

        async def offloaded():
            import asyncio
            future = get_future()
            return await asyncio.get_event_loop().run_in_executor(None, future.result)

        async def string_join_is_fine(parts):
            return ",".join(parts)
        """)
    assert ("m.batch", "blocking-in-async") in got
    assert ("m.spawn_and_wait", "blocking-in-async") in got
    assert not any(f == "m.string_join_is_fine" for f, _ in got)


def test_unbounded_wait_loop_needs_no_deadline_and_no_cap(tmp_path):
    got = _map(tmp_path, """
        import asyncio

        async def get_job(job_id):
            job = jobs[job_id]
            while job["status"] == "pending":
                await asyncio.sleep(0.1)
            return job

        async def get_job_with_deadline(job_id):
            job = jobs[job_id]
            deadline = time.monotonic() + 5
            while job["status"] == "pending" and time.monotonic() < deadline:
                await asyncio.sleep(0.1)
            return job

        async def retry_with_backoff():
            while True:
                try:
                    return await call()
                except Exception:
                    await asyncio.sleep(1)
                    continue
        """)
    assert ("m.get_job", "unbounded-wait-loop") in got
    assert not any(f in ("m.get_job_with_deadline", "m.retry_with_backoff") for f, _ in got
                   if _ == "unbounded-wait-loop")


def test_unbounded_module_container_needs_no_eviction_anywhere_in_file(tmp_path):
    got = _map(tmp_path, """
        _jobs = {}

        def create_job(job_id, payload):
            _jobs[job_id] = payload

        _evicted = {}

        def add_evicted(key, value):
            _evicted[key] = value

        def evict_old(key):
            _evicted.pop(key, None)
        """)
    assert ("m.create_job", "unbounded-module-container") in got
    assert not any(f in ("m.add_evicted", "m.evict_old") for f, r in got if r == "unbounded-module-container")


def test_client_per_call_but_not_when_built_once_and_reused(tmp_path):
    got = _map(tmp_path, """
        import httpx

        async def search(q):
            client = httpx.AsyncClient()
            resp = await client.get(f"https://api.example.com/search?q={q}")
            return resp.json()

        async def price_feed_loop():
            async with httpx.AsyncClient(timeout=5.0) as client:
                while True:
                    await client.get("/feed")
                    await asyncio.sleep(30)
        """)
    assert ("m.search", "client-per-call") in got
    assert not any(f == "m.price_feed_loop" and r == "client-per-call" for f, r in got)


def test_unchecked_response_needs_a_status_check_first(tmp_path):
    got = _map(tmp_path, """
        import requests

        def price_for(sku):
            resp = requests.get(f"https://pricing/price/{sku}")
            return resp.json()

        def price_checked(sku):
            resp = requests.get(f"https://pricing/price/{sku}")
            resp.raise_for_status()
            return resp.json()

        def price_checked_by_code(sku):
            resp = requests.get(f"https://pricing/price/{sku}")
            if resp.status_code != 200:
                raise RuntimeError("bad price")
            return resp.json()
        """)
    assert ("m.price_for", "unchecked-response") in got
    assert not any(f in ("m.price_checked", "m.price_checked_by_code") for f, r in got
                   if r == "unchecked-response")


def test_unsanitized_url_query_needs_params(tmp_path):
    got = _map(tmp_path, """
        import requests

        def unsafe_query(term):
            return requests.get(base + "?q=" + term)

        def unsafe_fstring(term):
            return requests.get(f"https://search?q={term}")

        def safe_query(term):
            return requests.get("https://search", params={"q": term})

        def safe_path(sku):
            return requests.get(f"https://pricing/price/{sku}")
        """)
    assert ("m.unsafe_query", "unsanitized-url-query") in got
    assert ("m.unsafe_fstring", "unsanitized-url-query") in got
    assert not any(f in ("m.safe_query", "m.safe_path") for f, r in got if r == "unsanitized-url-query")


@pytest.mark.skipif(sys.platform == "win32", reason="Windows strips trailing spaces in directory names, so 'hello guys ' cannot exist next to 'hello guys'")
def test_sibling_path_warning(tmp_path):
    real = tmp_path / "hello guys "
    (tmp_path / "hello guys").mkdir()
    shutil.copytree(FIX, real)
    w = paths.warnings(real)
    assert any("trailing whitespace" in x for x in w) and any("sibling" in x for x in w)
    assert paths.warnings(tmp_path / "hello guys") and not paths.warnings(FIX)


def _findings_map(tmp_path, src):
    tmp_path.mkdir(parents=True, exist_ok=True)
    (tmp_path / "m.py").write_text(textwrap.dedent(src))
    return RepoMap(tmp_path).load().findings


def test_sqlite_blocking_is_medium_but_a_real_network_db_stays_high(tmp_path):
    sqlite_findings = _findings_map(tmp_path / "a", """
        import sqlite3
        _db = sqlite3.connect(":memory:", check_same_thread=False)
        async def get_job(job_id):
            cur = _db.execute("SELECT * FROM jobs WHERE id=?", (job_id,))
            return cur.fetchone()
        """)
    pg_findings = _findings_map(tmp_path / "b", """
        import psycopg2
        _pg = psycopg2.connect("...")
        async def get_user(user_id):
            cur = _pg.execute("SELECT * FROM users WHERE id=%s", (user_id,))
            return cur.fetchone()
        """)
    mixed_findings = _findings_map(tmp_path / "c", """
        import sqlite3, psycopg2
        _db, _pg = sqlite3.connect(":memory:"), psycopg2.connect("...")
        async def get_job(job_id):
            return _db.execute("SELECT * FROM jobs WHERE id=?", (job_id,)).fetchone()
        async def get_user(user_id):
            return _pg.execute("SELECT * FROM users WHERE id=%s", (user_id,)).fetchone()
        """)

    def sev(findings, fn):
        return next(f.severity for f in findings if f.function == fn and f.rule == "blocking-in-async")

    assert sev(sqlite_findings, "m.get_job") == "medium"
    assert sev(pg_findings, "m.get_user") == "high"
    # Ambiguous (both DB libraries in one file): never downgrade, since the method-name match can't tell
    # which connection it resolved.
    assert sev(mixed_findings, "m.get_job") == "high" and sev(mixed_findings, "m.get_user") == "high"
