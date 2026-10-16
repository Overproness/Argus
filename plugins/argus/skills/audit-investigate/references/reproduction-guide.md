# Reproduction reference

### 3a. Python code: in-process

Import helpers from `auditor.repro.harness` (on PYTHONPATH when run through the CLI).

**Import the real code.** `repro` runs each file in its own process from the
repo root, under the repo's own environment when there is one (`.audit/venv`,
`.venv` or `venv`). If importing the module fails (a missing package, or a
framework version mismatch), run this once and retry:

```bash
python "<plugin-root>/scripts/auditor_cli.py" repro-env "<repo>"
```

It creates `<repo>/.audit/venv` from the repo's requirements, and later
`repro` runs use it. Only if that also fails may you work around import-time
plumbing, and only there: for example, replace a framework's route decorator
with a pass-through so the module imports. The function under test must still
be the repo's real code, imported or compiled from `abs_file`. Never replace
the function under test, or the library whose behaviour the finding is about
(the HTTP client of an `io-without-timeout`, the lock of a `lock-*`). Name the
workaround in the verdict's `env_workaround`.

**Leave the process as you found it.** Use pytest's `monkeypatch` fixture for
every patch, because it undoes itself even when the test fails. Never assign
to a shared module attribute by hand (`main.requests.get = ...` patches the
global `requests` module for every later test). Never `os.chdir`. Build file
paths with `repo_path("app", "main.py")`. If the code writes relative files
(a SQLite database next to the cwd), monkeypatch the connection to a
`tmp_path` database instead of moving the process.

| Rule | Helper | Assertion that means "reproduced" |
|---|---|---|
| blocking-in-async, sync-over-async, lock-across-await | `latency(t)` + `async with loop_monitor() as m` | `m.max_lag >= 0.8 * t` |
| io-without-timeout | `hang()` + `call_with_deadline(fn, 3)` | `not result["returned"]` (the call never gave up) |
| io-in-loop | `count_calls(module, "leaf_fn")` around one activation | `box["calls"] > 1`, ideally `== len(input)` |
| nested-loops | `scaling(fn, sizes, make_input)` | `fit.exponent >= 1.7` |
| recursion | call with a deep input inside `call_with_deadline` | `RecursionError` in `result["error"]`, or a depth bound found |
| deadline-cannot-preempt | `latency(t)` with `t` well above the deadline; time the caller end to end | elapsed `>= 0.8 * t`: the deadline did not fire when it should have |
| timeout-budget-exceeded | `latency(per_attempt)` + `count_connects()`; call the caller, then sleep briefly | the caller gives up at about its deadline while `connects` keeps growing after it returned |
| retry-without-backoff, unbounded-retry, retry-amplification | `with fail_connect(), count_connects() as box:` around one call (inside `call_with_deadline` when unbounded) | `connects` equals the predicted attempts (e.g. 12 for 3 × 4); no backoff: `max(gaps_s) < 0.05`; unbounded: still retrying at the deadline |
| hang-reaches-entry | `hang()` + `call_with_deadline(entry, 3)` | `not result["returned"]` |
| race-across-await | `asyncio.run(run_concurrently(handler, 50, ...))` on the real coroutine, with state reset through `monkeypatch` | lost updates: the final value is below 50, a balance goes negative, or the expensive branch ran more than once (`count_calls`) |
| lock-order-inversion | `threads_concurrently([lambda: f1(), lambda: f2()], 3)` | `stuck > 0`: both threads are still blocked at the deadline |
| lock-not-released | make the code between acquire and release raise (monkeypatch what it calls), call it once, then `lock.acquire(timeout=0.5)` | the call raised and the lock is still held (`lock.locked()`, or the acquire timed out) |
| lock-across-await (acquire form) | two concurrent calls: `call_with_deadline(lambda: asyncio.run(run_concurrently(handler, 2)), 3)` | `not result["returned"]`: the event loop deadlocked |
| mutate-while-iterating | seed the collection (e.g. 4 items that should all be removed), call once | items that should be gone remain (skipped elements), or `RuntimeError` |
| unbounded-threads | `with thread_growth() as g:` around K calls | `g["growth"] >= K` |
| unbounded-concurrency | `with peak_concurrency(module, "worker") as box:` (or on `asyncio.sleep`) with a large n | `box["peak"] == n`: no cap |
| cpu-heavy-in-async | `async with loop_monitor() as m:` around one call with a moderately large input | `m.max_lag` well above one tick (e.g. ≥ 0.2 s), growing with the input |
| sql-injection | monkeypatch the connection to a seeded `tmp_path` SQLite DB, call with `' OR '1'='1` (or a quote that breaks the syntax) | rows outside the filter come back, or `sqlite3.OperationalError` shows the input reached the SQL text |
| uncommitted-write | monkeypatch the connection to a `tmp_path` DB file, call once, read back through a second connection | the row is not visible to the second connection |
| fire-and-forget-task | `loop.set_exception_handler(...)` to capture, run the handler, let the task finish, `gc.collect()` | the task's exception reached only the loop's "never retrieved" handler: no caller saw it |
| blocking-in-async (future/thread) | `latency(t)` on the dependency, then `call_with_deadline(lambda: asyncio.run(handler(...)), 3)` around a call whose chain reaches `.result()`/`.join()` | `async with loop_monitor() as m:` around the same call shows `m.max_lag >= 0.8 * t`: the wait happened on the loop thread, not in a worker |
| unbounded-wait-loop | monkeypatch the condition so it never flips true, then `call_with_deadline(lambda: asyncio.run(handler(...)), 3)` | `not result["returned"]`: the loop never gives up |
| unbounded-module-container | call the growing function N times, inspect the module's container directly (`len(module._jobs)`) | `len(...) == N` with nothing evicted; or `with thread_growth()`/a memory probe if the container isn't importable |
| wallclock-interval | replace the target module's clock object with independently controlled wall and monotonic clocks; create an expiry, advance elapsed time past its TTL, and move wall time backward | a process-local cache/deadline remains valid after its elapsed lifetime; the same trigger expires correctly with a monotonic-clock fix. Do not patch the global clock for the whole test process. Persisted epoch timestamps need separate judgment. |
| buffered-response-slice | real HTTPX/requests client with a controlled chunked peer; count bytes consumed while the application returns its short slice | all peer bytes were read despite a short returned value; a streamed control stops after bounded chunks and returns the same prefix |
| unbounded-background-tasks | call the real route sequentially N times and count new pending `asyncio.all_tasks()` after responses; cancel and await them in cleanup | pending tasks grow with requests; awaiting the task or a real bounded admission policy prevents the backlog. Retention and worker-only semaphores are not admission caps. |
| client-per-call | `with peak_concurrency(module, "<ClientClass>.__init__")` (or `count_calls`) around N calls | `box["calls"] == N`: a new client every call, instead of one reused |
| unchecked-response | point the dependency at a `fault_server(status=500, body="not json")` (or monkeypatch the client to return a 500), call once | the function returns/caches the error body as if it were data, instead of raising |
| unsanitized-url-query | call with a value containing `&evil=1` or `\r\nX-Injected: 1`, inspect what the fake peer actually received (`fault_server`'s `srv.summary()["requests"]`, or a local `http.server` handler that echoes the raw request line) | the extra parameter/header reached the peer unescaped, changing the request |
| propagated:* | the trigger from `given.repro_file`, applied to the caller | the caller shows the effect (loop lag, missed deadline, attempt count) |

**Worked example** (race-across-await, the shape most investigators get wrong first):
```python
import asyncio
from auditor.repro.harness import evidence, run_concurrently, repo_path

def test_incr_lost_update(monkeypatch):
    import app.main as main
    monkeypatch.setattr(main, "counter", 0)  # reset shared state; never `main.counter = 0` by hand
    asyncio.run(run_concurrently(main.incr, 50))
    evidence(finding="race-across-await@app.incr:46", final=main.counter, expected=50)
    assert main.counter < 50  # lost updates: fewer than 50 increments landed
```
`run_concurrently` and `repo_path` are real functions in `auditor/repro/harness.py` on this repo's own
checkout — read that file if you are unsure a helper exists, never assume it is missing because a search
in the wrong tree didn't find it (see the wrong-tree check above).

**Never hand-run `pytest` directly**, even to debug. `python ".../auditor_cli.py" repro ... --file ...`
is the only supported way to run a reproduction: it sets `PYTHONPATH` to this plugin's own `scripts/`
directory first, so the test imports this repo's `auditor.repro.harness`, not a stale copy installed
elsewhere that may be missing a helper or have different rule names. Every run also prints which
`auditor` package it imported; if `repro`'s output warns of an `auditor_mismatch`, stop and report
`inconclusive` with `"blocked_by": "environment"` rather than trying to work around a helper that "doesn't
exist" — it exists in the right install.

### 3b. Every other language: fault server plus black-box or native probe

The dependency is replaced by `auditor.repro.faults.fault_server(...)`, a
local mock API (or TCP proxy with `upstream=(host, port)`) that injects faults
and counts every connection. The code under test runs in its own language and
is observed from outside by `auditor.repro.native`:

- **Black-box (preferred when it works):** run the real program (its CLI,
  service or test binary) with its dependency URL pointed at the fault server,
  via `run_target(cmd, env=..., timeout=..., heartbeat=r"...")`. Find how the
  URL is configured: an environment variable, config file, CLI flag or
  constructor argument.
- **Native probe (when the program cannot be pointed at the server, or you
  need one function):**
  1. `probe = scaffold(lang, "<name>")` creates
     `.audit/repros/native/<lang>/<name>/`, a side project that depends on the
     repo by path. The repo is never modified.
  2. Edit `probe.source` to call the function under test with
     `ARGUS_FAULT_URL`.
  3. Print one `@@evidence {json}` line per fact, including a
     `{"kind": "calling"}` line right before the call, so a hang is provably
     inside it.
  4. `build_probe(probe)`, then `run_probe(probe, env=..., timeout=...)`.

  Scaffold options:
  - rust `crate=` (workspace member dir), `deps=[...]`;
  - javascript/typescript `module=`;
  - java `sources=`, `classpath=[...]`;
  - c/cpp `sources=[...]`, `includes=[...]`;
  - go: no options, the module path is read from `go.mod`.

  `python "<plugin-root>/scripts/auditor_cli.py" probe <repo> --lang L --name N`
  prints the same scaffold from the shell.

| Rule | Fault server | Measurement that means "reproduced" |
|---|---|---|
| blocking-in-async, sync-over-async, deadline-cannot-preempt | `latency=t` | the program or probe prints a heartbeat line from a timer on the same loop; `res["max_heartbeat_gap_s"] >= 0.8 * t` |
| io-without-timeout, hang-reaches-entry | `hang=True` | `not res["returned"]` within the timeout, and the `calling` line was printed |
| timeout-budget-exceeded | `latency=per_attempt` | the caller returns at about its deadline while `srv.summary()["connections"]` keeps growing |
| retry-without-backoff, unbounded-retry, retry-amplification | `reset=True` (or `fail_first=n`) | `srv.summary()["connections"]` equals the predicted attempts; no backoff: `max_gap_s < 0.05` |
| io-in-loop | default (answers 200) | connections equal the number of items |
| panic-on-io-error, ignored-io-error | `reset=True` | the program crashes (non-zero `exit_code`, panic in `res["tail"]`) or carries on with an empty result |
| nested-loops, recursion | none | the probe prints `{"kind": "timing", "n": .., "seconds": ..}` per size; `auditor.trace.fit.fit_power(points).exponent >= 1.7` (or a deep input crashes) |

For example, a Rust function that never gives up on a dead peer:

```python
from auditor.repro.faults import fault_server
from auditor.repro.harness import evidence
from auditor.repro.native import build_probe, run_probe, scaffold

def test_fetch_price_hangs_on_dead_peer():
    probe = scaffold("rust", "fetch_price")   # then edit probe.source (src/main.rs) to call the function
    build_probe(probe)                        # compile outside the timed part
    with fault_server(hang=True) as srv:
        res = run_probe(probe, env={"ARGUS_FAULT_URL": srv.url}, timeout=5)
    evidence(finding="io-without-timeout@my_crate::client::fetch_price:42", returned=res["returned"])
    assert not res["returned"] and any(e.get("kind") == "calling" for e in res["evidence"])
```

If the language's toolchain is not installed (for example `cargo`, `go` or
`javac`), report `inconclusive` with the reason "toolchain X not
installed". Do not substitute a Python imitation of the code.

