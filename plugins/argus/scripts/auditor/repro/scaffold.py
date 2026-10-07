"""Pre-filled reproduction skeletons: the imports, fixture setup and evidence/assert stub for a finding's
rule, written before the investigator starts.

Most of an investigation's tokens go to setup that is the same for every finding of a given rule (which
harness helper, which imports, the shape of the assertion), not to the one fact specific to this finding
(the hypothesis: what exactly triggers it, here). `queue` writes one of these to `.audit/repros/` for every
queued item; the investigator's job narrows to filling in the `# TODO` lines, not building the test from a
blank file. A skeleton left unfilled fails loudly (an explicit `pytest.fail`), so it can never be mistaken
for a passing reproduction.
"""
from __future__ import annotations

import re

HEADER = '''"""Reproduction for {rule}@{function}:{line}.

{message}

Fill in the TODOs below: the trigger (what makes the effect happen here) and, where marked, the call
itself. Everything else is the usual shape for this rule - delete what you don't need, but don't change
the import list or the evidence()/assert shape without a reason.
"""
'''

FOOTER = '''
    # TODO: replace this line once the body above is filled in.
    import pytest
    pytest.fail("scaffold not filled in: write the actual trigger and assertion for this hypothesis")
'''


def _test_name(function: str) -> str:
    """The audited function's own name as an identifier. Ids look like `mod.fn`, `crate::mod::fn` or
    `pkg/mod.(*T).Method` depending on the language, and the result is used in both a filename (no `:` on
    Windows) and a `def`, so everything that isn't a name character is a separator or becomes `_`."""
    last = re.split(r"::|[./#:]", function)[-1]
    return re.sub(r"\W", "_", last) or "target"


# Each template is a (import_lines, body) pair. `{fid}` is substituted with the finding id, `{fn}` with
# the test function name, `{fname}` the audited function's own name.
TEMPLATES: dict[str, tuple[str, str]] = {
    "blocking-in-async": (
        "import asyncio\nfrom auditor.repro.harness import evidence, latency, loop_monitor",
        '''def test_{fn}_blocks_the_loop():
    # TODO: latency(t) should match how this call is reached from a real caller (patch the right socket/client).
    with latency(1.0):
        async def run():
            async with loop_monitor() as m:
                await asyncio.sleep(0.05)
                # TODO: await the real path to `{fname}` here (import the module, call it).
                await asyncio.sleep(0.05)
            return m.max_lag
        lag = asyncio.run(run())
    evidence(finding="{fid}", max_lag_s=lag)
    assert lag >= 0.8  # the injected latency showed up as event-loop lag
'''),
    "cpu-heavy-in-async": (
        "import asyncio\nfrom auditor.repro.harness import evidence, loop_monitor",
        '''def test_{fn}_holds_the_loop():
    async def run():
        async with loop_monitor() as m:
            await asyncio.sleep(0.02)
            # TODO: call `{fname}` with an input large enough to take a noticeable slice of time.
            await asyncio.sleep(0.02)
        return m.max_lag
    lag = asyncio.run(run())
    evidence(finding="{fid}", max_lag_s=lag)
    assert lag >= 0.2  # grows with the input; raise this and the input size together if it's still small
'''),
    "io-without-timeout": (
        "from auditor.repro.harness import evidence, hang, call_with_deadline, refuse_remote",
        '''def test_{fn}_hangs_on_dead_peer():
    with refuse_remote(), hang():
        # TODO: call_with_deadline wraps a *blocking* call; for an async entry point wrap
        # `lambda: asyncio.run({fname}(...))` instead.
        res = call_with_deadline(lambda: None, 3)  # TODO: replace `lambda: None` with the real call
    evidence(finding="{fid}", returned=res["returned"])
    assert not res["returned"]
'''),
    "hang-reaches-entry": (
        "from auditor.repro.harness import evidence, hang, call_with_deadline, refuse_remote",
        '''def test_{fn}_hangs_on_dead_peer():
    with refuse_remote(), hang():
        res = call_with_deadline(lambda: None, 3)  # TODO: replace with the real entry-point call
    evidence(finding="{fid}", returned=res["returned"])
    assert not res["returned"]
'''),
    "io-in-loop": (
        "from auditor.repro.harness import evidence, count_calls",
        '''def test_{fn}_is_n_plus_one():
    # TODO: replace `module, "leaf_fn"` with the module object and the name of the function actually
    # called once per item (the innermost call in the finding's chain).
    with count_calls(None, "leaf_fn") as box:
        pass  # TODO: call `{fname}` once, with an input of a known size N
    evidence(finding="{fid}", calls=box["calls"])
    assert box["calls"] > 1
'''),
    "race-across-await": (
        "import asyncio\nfrom auditor.repro.harness import evidence, run_concurrently",
        '''def test_{fn}_loses_updates(monkeypatch):
    # TODO: import the real module and reset its shared state through monkeypatch (never assign to it by hand).
    N = 50
    # TODO: results = asyncio.run(run_concurrently(the_real_coroutine_function, N, ...))
    results = asyncio.run(run_concurrently(lambda: asyncio.sleep(0), N))
    final = None  # TODO: read the shared state back
    evidence(finding="{fid}", final=final, expected=N)
    assert final != N  # lost updates: fewer than N changes landed
'''),
    "lock-order-inversion": (
        "from auditor.repro.harness import evidence, threads_concurrently",
        '''def test_{fn}_deadlocks():
    # TODO: two callables, each taking the same two locks in the opposite order.
    res = threads_concurrently([lambda: None, lambda: None], 3)
    evidence(finding="{fid}", **res)
    assert res["stuck"] > 0
'''),
    "lock-not-released": (
        "from auditor.repro.harness import evidence",
        '''def test_{fn}_leaks_the_lock(monkeypatch):
    # TODO: monkeypatch what the critical section calls so it raises, then call `{fname}` once.
    # TODO: import the lock object and assert it is still held (`lock.locked()`, or acquire(timeout=0.5) fails).
    locked_after = None
    evidence(finding="{fid}", locked_after=locked_after)
    assert locked_after
'''),
    "lock-across-await": (
        "import asyncio\nfrom auditor.repro.harness import evidence, run_concurrently, call_with_deadline",
        '''def test_{fn}_deadlocks_the_loop():
    # TODO: two concurrent calls to the real async handler that takes this lock.
    res = call_with_deadline(lambda: asyncio.run(run_concurrently(lambda: asyncio.sleep(0), 2)), 3)
    evidence(finding="{fid}", returned=res["returned"])
    assert not res["returned"]
'''),
    "mutate-while-iterating": (
        "from auditor.repro.harness import evidence",
        '''def test_{fn}_skips_elements():
    # TODO: seed the collection with items that should ALL be removed/processed, call `{fname}` once.
    items = []
    remaining = len(items)
    evidence(finding="{fid}", remaining=remaining, expected=0)
    assert remaining > 0  # some were skipped because the list shrank under the loop
'''),
    "unbounded-threads": (
        "from auditor.repro.harness import evidence, thread_growth",
        '''def test_{fn}_leaks_threads():
    K = 20
    with thread_growth() as g:
        for _ in range(K):
            pass  # TODO: call `{fname}` once per iteration
    evidence(finding="{fid}", **g)
    assert g["growth"] >= K
'''),
    "unbounded-concurrency": (
        "from auditor.repro.harness import evidence, peak_concurrency",
        '''def test_{fn}_has_no_cap():
    n = 200
    # TODO: replace `module, "worker"` with the module and the function/coroutine run once per item.
    with peak_concurrency(None, "worker") as box:
        pass  # TODO: call `{fname}` with n items
    evidence(finding="{fid}", **box)
    assert box["peak"] == n
'''),
    "sql-injection": (
        "import sqlite3\nfrom auditor.repro.harness import evidence",
        '''def test_{fn}_is_injectable(tmp_path, monkeypatch):
    db = sqlite3.connect(str(tmp_path / "t.db"))
    db.execute("CREATE TABLE items (id INTEGER PRIMARY KEY, name TEXT)")
    db.executemany("INSERT INTO items (name) VALUES (?)", [("a",), ("b",), ("secret",)])
    db.commit()
    # TODO: monkeypatch the module's connection to `db`, then call `{fname}` with an injection payload
    # such as "' OR '1'='1" and compare the rows returned against a normal, non-matching filter.
    normal_rows = injected_rows = None
    evidence(finding="{fid}", normal_rows=normal_rows, injected_rows=injected_rows)
    assert injected_rows is not None and injected_rows > (normal_rows or 0)
'''),
    "uncommitted-write": (
        "import sqlite3\nfrom auditor.repro.harness import evidence",
        '''def test_{fn}_is_not_visible_elsewhere(tmp_path, monkeypatch):
    path = str(tmp_path / "t.db")
    db = sqlite3.connect(path)
    # TODO: monkeypatch the module's connection to `db`, call `{fname}` once to write a row.
    other = sqlite3.connect(path)
    visible = other.execute("SELECT COUNT(*) FROM items").fetchone()[0]  # TODO: adjust the table/columns
    evidence(finding="{fid}", visible_rows_second_conn=visible)
    assert visible == 0
'''),
    "fire-and-forget-task": (
        "import asyncio, gc\nfrom auditor.repro.harness import evidence",
        '''def test_{fn}_exception_never_reaches_a_caller():
    loop = asyncio.new_event_loop()
    caught = []
    loop.set_exception_handler(lambda l, ctx: caught.append(ctx))
    async def run():
        pass  # TODO: call `{fname}`, then await asyncio.sleep() past when its task should fail
    loop.run_until_complete(run())
    gc.collect()
    evidence(finding="{fid}", loop_handler_calls=len(caught))
    assert caught  # the failure reached only the loop's handler, not any caller
'''),
    "unbounded-wait-loop": (
        "import asyncio\nfrom auditor.repro.harness import evidence, call_with_deadline",
        '''def test_{fn}_never_gives_up(monkeypatch):
    # TODO: arrange for the condition this loop polls to never become true (monkeypatch it, or seed state
    # so it stays "pending" forever).
    res = call_with_deadline(lambda: asyncio.run(_noop()), 3)
    evidence(finding="{fid}", returned=res["returned"])
    assert not res["returned"]

async def _noop():  # TODO: replace with `await {fname}(...)`
    pass
'''),
    "unbounded-module-container": (
        "from auditor.repro.harness import evidence",
        '''def test_{fn}_grows_without_bound():
    import importlib
    # TODO: import the real module (`m = importlib.import_module("...")`) and call `{fname}` N times.
    N = 50
    size_before = size_after = None
    evidence(finding="{fid}", before=size_before, after=size_after, calls=N)
    assert size_after == size_before + N  # nothing was ever evicted
'''),
    "client-per-call": (
        "from auditor.repro.harness import evidence, count_calls",
        '''def test_{fn}_builds_a_new_client_every_time():
    import httpx  # TODO: or the library the finding names
    N = 5
    with count_calls(httpx, "AsyncClient") as box:  # TODO: patch the exact constructor used
        pass  # TODO: call `{fname}` N times
    evidence(finding="{fid}", **box)
    assert box["calls"] == N
'''),
    "unchecked-response": (
        "from auditor.repro.harness import evidence",
        '''def test_{fn}_trusts_an_error_body(monkeypatch):
    # TODO: monkeypatch the HTTP call `{fname}` makes to return a 500 with a non-JSON (or empty) body.
    result = None  # TODO: call `{fname}` and capture what it returns/caches
    evidence(finding="{fid}", result=result)
    assert result is not None  # it returned/cached the error as if it were a success
'''),
    "unsanitized-url-query": (
        "import threading, http.server\nfrom auditor.repro.harness import evidence",
        '''def test_{fn}_sends_the_value_unescaped():
    seen = {{}}
    class H(http.server.BaseHTTPRequestHandler):
        def do_GET(self):
            seen["path"] = self.path
            self.send_response(200); self.send_header("Content-Length", "2"); self.end_headers()
            self.wfile.write(b"{{}}")
        def log_message(self, *a): pass
    srv = http.server.ThreadingHTTPServer(("127.0.0.1", 0), H)
    threading.Thread(target=srv.serve_forever, daemon=True).start()
    # TODO: point `{fname}` at http://127.0.0.1:{{port}} and call it with a value containing "&evil=1".
    srv.shutdown()
    evidence(finding="{fid}", path_seen=seen.get("path"))
    assert "evil" in (seen.get("path") or "")
'''),
}

GENERIC = (
    "from auditor.repro.harness import evidence",
    '''def test_{fn}():
    # TODO: no ready-made template for "{rule_raw}". Read the rule's row in agents/investigator.md's
    # table (section 3a/3b) for the matching helper, or build the smallest test that triggers the effect.
    pass
''')


def generate(item: dict) -> str:
    """The starter file text for one queue item's PRIMARY finding (item["findings"][0])."""
    f = item["findings"][0]
    rule = f["rule"].split(":", 1)[-1]  # propagated:<rule> uses the base rule's template
    imports, body = TEMPLATES.get(rule, GENERIC)
    fn = _test_name(item["function"])
    fname = item["function"].rsplit(".", 1)[-1]
    header = HEADER.format(rule=f["rule"], function=item["function"], line=f.get("line", item.get("line", "?")),
                           message=f.get("message", ""))
    rendered = body.format(fn=fn, fname=fname, fid=f["id"], rule_raw=f["rule"])
    return header + "\n" + imports + "\n\n\n" + rendered


def write(item: dict, repro_dir) -> "Path":
    """Write the skeleton to `<repro_dir>/test_<function>.py` unless a file is already there (never
    overwrite an investigator's work in progress)."""
    from pathlib import Path
    repro_dir = Path(repro_dir)
    repro_dir.mkdir(parents=True, exist_ok=True)
    path = repro_dir / f"test_{_test_name(item['function'])}.py"
    if not path.exists():
        path.write_text(generate(item), encoding="utf8")
    return path
