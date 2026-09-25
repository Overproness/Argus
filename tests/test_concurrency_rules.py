"""Recall on a FastAPI app with 16 planted concurrency and reliability bugs, and the rules that find them."""
import shutil
import textwrap

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


def test_sibling_path_warning(tmp_path):
    real = tmp_path / "hello guys "
    (tmp_path / "hello guys").mkdir()
    shutil.copytree(FIX, real)
    w = paths.warnings(real)
    assert any("trailing whitespace" in x for x in w) and any("sibling" in x for x in w)
    assert paths.warnings(tmp_path / "hello guys") and not paths.warnings(FIX)
