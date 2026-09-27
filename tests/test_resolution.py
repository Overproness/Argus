"""Resolution and classification: the gaps a seeded service (tests/fixtures/orderdesk) exposed.

Each case is a small repo written to a temp dir, mapped, and checked for the findings the change is about:
constants held in another module, I/O reached through to_thread, methods called on typed instances, retry
and fixed-size loops that are not N+1 or O(n^2), error paths that skip a delay, CPU-heavy library calls, and
patterns that live outside a function body.
"""
import json
import subprocess
import sys
import textwrap

import pytest
from conftest import ROOT

sys.path.insert(0, str(ROOT / "plugins" / "argus" / "scripts"))
from auditor.analysis import RepoMap  # noqa: E402

CLI = ROOT / "plugins" / "argus" / "scripts" / "auditor_cli.py"


def mapped(tmp_path, files: dict[str, str]) -> set[tuple[str, str]]:
    for rel, src in files.items():
        p = tmp_path / rel
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_text(textwrap.dedent(src))
    m = RepoMap(tmp_path).load()
    mapped.last = m
    return {(f.rule, f.function) for f in m.findings}


def messages(rule: str) -> list[str]:
    return [f.message for f in mapped.last.findings if f.rule == rule]


SETTINGS = """
    CALL_TIMEOUT = 10.0
    ATTEMPTS = 3
    DEADLINE = 2.0
"""


def test_constants_from_another_module_bound_timeouts_and_retries(tmp_path):
    got = mapped(tmp_path, {"app/settings.py": SETTINGS, "app/svc.py": """
        import asyncio
        import httpx
        from . import settings

        client = httpx.AsyncClient(timeout=settings.CALL_TIMEOUT)

        async def fetch(key):
            for attempt in range(settings.ATTEMPTS):
                try:
                    return (await client.get(f"/k/{key}")).json()
                except httpx.HTTPError:
                    if attempt == settings.ATTEMPTS - 1:
                        raise
                    await asyncio.sleep(0.1 * 2 ** attempt)

        async def handler(key):
            return await asyncio.wait_for(fetch(key), timeout=settings.DEADLINE)
    """})
    assert ("timeout-budget-exceeded", "app.svc.handler") in got
    assert any("3 attempts × 10 s" in m for m in messages("timeout-budget-exceeded"))


def test_deadline_held_in_a_constant_cannot_preempt_blocking_code(tmp_path):
    got = mapped(tmp_path, {"app/settings.py": SETTINGS, "app/svc.py": """
        import asyncio
        import requests
        from . import settings

        async def work():
            return requests.get("http://x/y", timeout=5).text

        async def handler():
            return await asyncio.wait_for(work(), settings.DEADLINE)
    """})
    assert ("deadline-cannot-preempt", "app.svc.handler") in got


def test_retry_counts_in_constants_multiply(tmp_path):
    mapped(tmp_path, {"app/settings.py": SETTINGS, "app/svc.py": """
        import httpx
        from . import settings

        async def call(client):
            for attempt in range(settings.ATTEMPTS):
                try:
                    return await client.get("/x", timeout=1)
                except httpx.HTTPError:
                    if attempt == settings.ATTEMPTS - 1:
                        raise

        async def outer(client):
            for attempt in range(settings.ATTEMPTS):
                try:
                    return await call(client)
                except httpx.HTTPError:
                    continue
    """})
    assert any("up to 9 attempts" in m for m in messages("retry-amplification"))


def test_io_through_to_thread_still_counts_in_a_loop_but_does_not_block(tmp_path):
    got = mapped(tmp_path, {"app/db.py": """
        import asyncio
        import sqlite3

        conn = sqlite3.connect(":memory:", check_same_thread=False)

        def _query(sql, params=()):
            return [dict(r) for r in conn.execute(sql, params).fetchall()]

        async def get(pk):
            return await asyncio.to_thread(_query, "select * from t where id = ?", (pk,))

        async def summary(ids):
            out = []
            for i in ids:
                out.append(await get(i))
            return out
    """})
    assert ("io-in-loop", "app.db.summary") in got
    assert ("blocking-in-async", "app.db.get") not in got
    assert ("io-in-loop", "app.db._query") not in got  # execute() is the comprehension's iterable: runs once


def test_method_on_a_typed_module_instance_resolves_despite_a_common_name(tmp_path):
    got = mapped(tmp_path, {"app/limit.py": """
        import time

        class Throttle:
            def wait(self):
                time.sleep(0.05)
    """, "app/api.py": """
        from .limit import Throttle

        throttle = Throttle()

        async def handler():
            throttle.wait()
            return 1
    """})
    assert ("blocking-in-async", "app.api.handler") in got


def test_delegating_to_a_library_object_is_not_recursion(tmp_path):
    got = mapped(tmp_path, {"app/c.py": """
        import httpx

        class Client:
            def __init__(self):
                self._client = httpx.AsyncClient(timeout=3)

            async def aclose(self):
                await self._client.aclose()
    """})
    assert ("recursion", "app.c.Client.aclose") not in got


def test_retry_loops_are_not_n_plus_one_and_do_not_nest(tmp_path):
    got = mapped(tmp_path, {"app/svc.py": """
        import httpx

        async def fetch(client, key):
            for attempt in range(4):
                try:
                    return (await client.get(f"/k/{key}", timeout=2)).json()
                except httpx.HTTPError:
                    if attempt == 3:
                        return None

        async def reserve_all(client, lines):
            done = []
            for line in lines:
                for attempt in range(3):
                    try:
                        done.append(await client.post("/r", json=line, timeout=2))
                        break
                    except httpx.HTTPError:
                        if attempt == 2:
                            raise
            return done

        DAYS = ["mon", "tue", "wed"]

        def grid():
            return [(d, h) for d in DAYS for h in range(24)]

        def pairs(items):
            return [[a * b for b in items] for a in items]

        def tail_pairs(items):
            out = []
            for i in range(len(items)):
                for j in (i + 1, len(items)):
                    out.append((i, j))
            return out
    """})
    assert ("io-in-loop", "app.svc.fetch") not in got
    assert ("io-in-loop", "app.svc.reserve_all") in got  # the per-line loop is still N+1
    assert ("nested-loops", "app.svc.reserve_all") not in got
    assert ("nested-loops", "app.svc.grid") not in got
    assert ("nested-loops", "app.svc.pairs") in got
    assert ("nested-loops", "app.svc.tail_pairs") not in got  # inner loop walks a 2-item literal


def test_error_path_that_skips_the_delay_is_an_unbounded_retry(tmp_path):
    got = mapped(tmp_path, {"app/w.py": """
        import asyncio
        import httpx

        async def feed(client, apply):
            while True:
                try:
                    resp = await client.get("/feed", timeout=5)
                    resp.raise_for_status()
                except httpx.HTTPError:
                    continue
                await apply(resp.json())
                await asyncio.sleep(30)
    """})
    assert ("unbounded-retry", "app.w.feed") in got
    assert any("no delay" in m for m in messages("unbounded-retry"))


def test_password_hashing_on_the_async_path_is_cpu_heavy(tmp_path):
    got = mapped(tmp_path, {"app/auth.py": """
        import hashlib

        def digest(pw, salt):
            return hashlib.pbkdf2_hmac("sha256", pw, salt, 390_000)

        async def login(pw, salt):
            return digest(pw, salt)
    """})
    assert ("cpu-heavy-in-async", "app.auth.login") in got
    assert ("blocking-in-async", "app.auth.login") not in got


def test_patterns_outside_the_body_and_across_lines(tmp_path):
    got = mapped(tmp_path, {"app/p.py": """
        import re
        import time

        CODE = re.compile(r"^([A-Z0-9]+-?)+$")

        def check(code):
            return bool(CODE.match(code))

        def line_total(unit_price: float, qty: int) -> float:
            return unit_price * qty

        class Pace:
            def wait(self):
                now = time.time()
                delay = self.last + 0.1 - now
                if delay > 0:
                    time.sleep(delay)

        async def search(query, term):
            return await query(f"select * from items where name like '%{term}%'")

        async def by_ids(query, ids):
            placeholders = ",".join("?" for _ in ids)
            return await query(f"select * from items where id in ({placeholders})", ids)

        def _run(conn, sql, params=()):
            return conn.execute(sql, params).fetchall()

        def everything(conn):
            return _run(conn, "select id, name from items order by id")

        def counted(conn):
            return conn.execute("select count(*) from items").fetchone()
    """})
    assert ("redos-regex", "app.p.check") in got
    assert ("float-money", "app.p.line_total") in got
    assert ("wallclock-interval", "app.p.Pace.wait") in got
    assert ("sql-injection", "app.p.search") in got
    assert ("sql-injection", "app.p.by_ids") not in got
    assert ("unbounded-query", "app.p.everything") in got
    assert ("unbounded-query", "app.p._run") not in got
    assert ("unbounded-query", "app.p.counted") not in got


def test_depth_guarded_recursion_is_not_reported(tmp_path):
    got = mapped(tmp_path, {"app/t.py": """
        def render(node, depth=0, max_depth=6):
            out = [node["name"]]
            if depth >= max_depth:
                return out
            for c in node.get("children", []):
                out += render(c, depth + 1, max_depth)
            return out

        def walk(node):
            return [node] + [x for c in node["kids"] for x in walk(c)]
    """})
    assert ("recursion", "app.t.render") not in got
    assert ("recursion", "app.t.walk") in got


INJECT_APP = """
import asyncio
import time


class Slow:
    def fetch(self):          # stands in for a blocking client call; the test double is instant
        return 1


def _decorate(fn):
    return fn


slow = Slow()


@_decorate
async def on_loop():
    return slow.fetch()


async def off_loop():
    return await asyncio.to_thread(slow.fetch)


async def main():
    await on_loop()
    await off_loop()


asyncio.run(main())
"""


@pytest.mark.skipif(sys.version_info < (3, 12), reason="tracer needs sys.monitoring")
def test_injected_latency_proves_which_calls_block_the_loop(tmp_path):
    (tmp_path / "app.py").write_text(INJECT_APP)
    subprocess.run([sys.executable, CLI, "map", tmp_path], check=True, capture_output=True)
    # the map does not know Slow.fetch is blocking: mark it as a boundary the way a real client call is
    m = json.loads((tmp_path / ".audit" / "map.json").read_text())
    fid = next(f["id"] for f in m["functions"] if f["qualname"] == "app.Slow.fetch")
    m["boundaries"].append({"function": "app.Slow.fetch", "lang": "python", "location": f"app.py:{fid.split(':')[1]}",
                            "kind": "http", "category": "net", "blocking": True, "context": "sync"})
    m["findings"] += [
        {"rule": "blocking-in-async", "severity": "high", "confidence": "exact", "lang": "python",
         "function": fn, "file": "app.py", "line": 0, "message": "", "chain": [fn, "app.Slow.fetch"],
         "reached_from": []} for fn in ("app.on_loop", "app.off_loop")]
    (tmp_path / ".audit" / "map.json").write_text(json.dumps(m))
    subprocess.run([sys.executable, CLI, "trace", tmp_path, "--inject-latency", "150", "--",
                    sys.executable, "app.py"], check=True, capture_output=True, timeout=120)
    ev = {f["function"]: f["evidence"] for f in json.loads((tmp_path / ".audit" / "trace.json").read_text())["findings"]
          if f["rule"] == "blocking-in-async"}
    assert ev["app.on_loop"]["status"] == "confirmed"  # decorated: matched by its def line
    assert "injected" in ev["app.on_loop"]["detail"]
    assert ev["app.off_loop"]["status"] == "not-observed"
    assert "off the loop thread" in ev["app.off_loop"]["detail"]
