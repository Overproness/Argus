# What Argus is good at (as of 2026-10-03, commit `d938e42`)

This is a field guide for testing Argus rigorously: what it catches, what it
explicitly doesn't, and how to check each claim yourself. It reflects the code
at HEAD, not aspirations — commands below were run to confirm each section.

Test suite: **180 passed, 1 skipped** (`python3 -m pytest -q tests`, ~108s).
Skip is `test_runner_collects_verdicts`-adjacent fixture gated on Python 3.12+.

## How it works, in one paragraph

`map` parses the repo with tree-sitter per language, resolves calls into a
graph, classifies known I/O/blocking/CPU call shapes as "boundaries", and runs
two kinds of rule on top: **structural** (call-graph propagation: does a
blocking call reach an async path, does a deadline wrap something that can't
be preempted, does an unbounded wait reach an entry point) and **pattern**
(regex-over-source heuristics for things no call graph captures, like SQL
built by string formatting). Every finding is a *lead*, not a verdict.
`queue`/`record`/`repro` turn leads into reproductions that actually run and
either pass (confirmed) or don't. `report` writes the final scorecard.

## Languages

Rust, Python, JavaScript/TypeScript, Go, Java, Kotlin, Scala, C#, Swift,
C/C++, Ruby, PHP (`auditor_cli.py langs` lists exact grammar/extension
support). Structural rules (blocking-in-async, timeouts, deadlines, retries)
work in every one of these. The newer concurrency/injection rules below are
**Python-only** unless noted, plus `sql-injection` and `unbounded-query`
which also cover JS/TS/Java/Go.

## Rule catalog — what it actually flags

### Structural, cross-language (call-graph propagation, `effects.py`/`analysis.py`)
| Rule | What it means |
|---|---|
| `blocking-in-async` | A blocking call (sync HTTP, sleep, sync DB, sync file I/O, CPU-bound crypto) reached from an async/event-loop path, directly or through a call chain. |
| `io-without-timeout` | A network/DB call with no explicit, client-level, or library-default timeout. |
| `io-in-loop` | I/O inside a loop (N+1 pattern); severity depends on whether the loop scales with input size vs. runs once forever. |
| `deadline-cannot-preempt` | A deadline/timeout wraps code that blocks the thread or loop it runs on, so the deadline can't actually fire in time. |
| `timeout-budget-exceeded` | An inner call/retry chain can take longer than the deadline the caller wraps around it. |
| `hang-reaches-entry` | An entry point (route handler, consumer, `main`) has no bound anywhere on its path to an unbounded wait. |
| `unbounded-retry` / `retry-without-backoff` | Retry loops with no cap, or with no delay between attempts. |
| `retry-amplification` | Nested retry layers multiply into a huge number of attempts against one dependency. |
| `nested-loops` | Loop nesting depth ≥2 whose trip counts scale with input — candidate for complexity fitting. |
| `cpu-heavy-in-async` | CPU-bound work (including recursion) running inline on an async/event-loop path with nothing yielding. |
| `recursion` | A recursive call cycle exists; check depth is bounded on adversarial input. |
| `panic-on-io-error` (Rust) / `ignored-io-error` (Go) | A network/DB call's result is unwrapped/discarded rather than handled. |

### Pattern rules, regex-over-source (`langs/patterns.py`)
| Rule | Languages | What it means |
|---|---|---|
| `sql-injection` | Python, JS, TS, Java, Go | SQL text built by f-string/concat/`%`/`.format()` with a caller-influenced value. Excludes cases where only *structure* (placeholders, column names) is interpolated, not a value. |
| `unbounded-query` | Python, JS, TS, Java, Rust | `SELECT` with no `WHERE`/`LIMIT`, `.fetchall()`, `.objects.all()` — excluding generic query helpers that just run whatever SQL their caller passed in. |
| `float-money` | all | A money-shaped variable/field typed or assigned as a float/double. |
| `wallclock-interval` | Python, JS, TS, Java, Rust | An interval computed by subtracting two wall-clock reads (`time.time()`, `Date.now()`) instead of a monotonic clock — including the two-step `now = time.time(); ... - now` form. |
| `redos-regex` | all | A regex with a repeated group containing another repeat (catastrophic backtracking shape) — including ones defined as a module-level constant and used elsewhere. |
| `panic-on-external-data` | Go, Rust | Parsing/decoding external input and unwrapping without checking the error. |
| `unbounded-channel` | Python, Rust, Java, Go | An unbounded queue/channel: a fast producer grows memory without limit. |
| `fire-and-forget-task` | Python, Rust, Java | A spawned task/thread whose handle is dropped — errors vanish, nothing waits for it. |
| `backoff-without-jitter` | all | Retries that back off but never add random jitter (thundering herd on recovery). |

### Python concurrency/correctness rules (new, `langs/python.py`)
| Rule | What it means | How to trigger it in a test file |
|---|---|---|
| `race-across-await` | Shared (module-global) state is read, then written, with an `await` in between and no lock — the classic FastAPI/asyncio lost-update or check-then-act bug. | `async def f(): if k not in cache: await X(); cache[k]=...` |
| `lock-order-inversion` | Two functions (or a function and something it calls) take the same two locks in opposite order — a deadlock waiting to happen. | `f()` does `with a: with b:`, `g()` does `with b: with a:` |
| `lock-not-released` | `lock.acquire()`/`release()` used by hand with no `release()` guaranteed in a `finally` — an exception leaks the lock forever. | `lock.acquire(); risky(); lock.release()` |
| `lock-across-await` | A `threading.Lock` held across an `await`, or acquired/released on a coroutine with no `finally` — blocks the event loop thread itself. | `with threading_lock: await asyncio.sleep(...)` inside `async def` |
| `mutate-while-iterating` | A collection is mutated (append/remove/pop/etc.) while a `for` loop iterates over it directly (not a copy). | `for o in orders: orders.remove(o)` |
| `unbounded-threads` | A new OS thread started per call, never joined, no pool/cap. | `Thread(target=...).start()` with no `.join()` and no pool |
| `unbounded-concurrency` | `asyncio.gather(*[...])` over a caller-controlled list with no semaphore/cap. | `await asyncio.gather(*[work(i) for i in items])` |
| `uncommitted-write` | An `INSERT`/`UPDATE`/`DELETE` executed on a connection that's never committed (and isn't in autocommit mode or a `with conn:` block). | `conn.execute("INSERT ..."); return` (no `.commit()`) |
| Python `file` boundary | Sync `open()`/`os.*`/`shutil.*`/`Path.read_text()` etc. now classified as blocking I/O, so they feed `blocking-in-async` when called from `async def`. | sync file read inside an async handler |
| sqlite3 default timeout | sqlite3 calls are no longer flagged as "waits forever" — they're bounded by the 5s `busy_timeout` default, which cut a false-positive category. | — |
| Startup-hook downgrade | A blocking call inside a FastAPI/Starlette `@app.on_event("startup")` hook (or similar) is downgraded to low severity — it delays boot, it doesn't freeze live requests. | — |

### JavaScript/Go (lighter touch)
- JS/TS: sync `fs.*Sync`, `child_process.*Sync`, `Atomics.wait`, and now **CPU-bound crypto** (`crypto.pbkdf2Sync`, `bcrypt.hashSync`, etc.) are classified as blocking.
- Go: `loop_hazards` hook (goroutine/loop-specific issues) — see `golang.py` for the current shape.
- Rust: `lock_across_await` hook for `std::sync::Mutex` guards held across `.await`.

### Reads config/constants, not just literals
Timeout and retry values written as a named constant (`CHECKOUT_DEADLINE = 3.0`,
Rust `const MAX_RETRIES: u32 = 5;`, etc.) are now resolved repo-wide, so a
deadline held in a settings module isn't invisible to the deadline/retry math
the way it used to be. Still blind to env vars and runtime config objects.

## Reproduction / verdict pipeline — what "proven" actually means

- `repro-env` builds a venv with the target repo's own dependencies so tests
  import the real app, not a stub.
- `queue` groups findings by function (one investigator per function, up to
  5 findings riding along, low-severity ones free), ranked by severity ×
  confidence × trace evidence × hotspot score.
- Each reproduction file runs in its **own process**, state is restored
  between tests in a file (`isolation.py`), and results are merged under a
  lock so parallel investigators can't clobber each other's output.
- `queue`/`report` warn when the repo path has stray whitespace or a
  near-identical sibling directory exists — the exact mistake that used to
  send 30% of a budget into the wrong tree.
- A "confirmed" verdict is downgraded to inconclusive automatically if its own
  reproduction doesn't pass on a cold rerun. Tooling-caused inconclusives
  (wrong tree, a broken repro run) are retried once automatically.
- Default budget: 15 investigations total, 6 per round, 3 rounds.

**Validated result (2026-09-25 run on the 16-bug FastAPI fixture,
`~/Documents/hello guys ` — trailing space, see
`tests/fixtures/py_concurrency/`):** static map found all 16 planted bugs
(blocking-in-async, races, both deadlocks, leaked lock, SQL injection,
uncommitted write, mutate-while-iterating, unbounded thread/gather, startup
hang, CPU-bound recursion). Investigation round proved **15 of 16** with
real reproductions passing cold; the 16th (`unbounded-concurrency@fanout`)
was a deferred, correctly-scored lead that didn't fit the budget. Zero
wrong-tree investigations, zero tooling-caused inconclusives.

## What it is explicitly NOT good at (don't test for these and call it a miss)

- **Anything needing live infrastructure.** No real exchange, no production
  DB, no real third-party API — it refuses and asks for a mock/sandbox.
- **Config/env-var-sourced timeouts and retry counts.** If the number comes
  from `os.environ`, a YAML file, or is computed at runtime, the static map
  reports it as unbounded/unknown rather than guessing. This is correct
  caution, not a bug — don't count it as a miss if the tool says "I can't
  tell, check the code."
- **Non-Python concurrency bugs beyond the basics.** Races, deadlocks, lock
  ordering — those new rules are Python-only. Go/Rust/Java concurrency bugs
  rely on the older structural rules plus `clippy`/linter import, not a
  dedicated race/deadlock detector.
- **Security beyond SQL injection.** No XSS, SSRF, auth-bypass, path
  traversal, deserialization, or secrets-in-code rules. SQL injection is the
  one security rule that exists, and it's deliberately narrow (string-built
  SQL with an interpolated value, not every dynamic query).
- **Business-logic bugs.** It finds reliability/concurrency *shapes* in code,
  not "this calculation is wrong" or "this violates a spec."
- **Pure runtime/semantic bugs with no I/O or concurrency shape** (off-by-one,
  wrong comparison operator, typos in logic) — out of scope by design.

## How to test it rigorously

1. **Recall test**: write (or reuse) a small app with N planted bugs from the
   tables above, run `map`, and count how many get *any* lead. The fixture at
   `tests/fixtures/py_concurrency/app/main.py` plus
   `tests/test_concurrency_rules.py` is exactly this, already automated — add
   bugs to it and extend the `PLANTED` set to keep raising the bar.
2. **Precision test**: for each rule, write one deliberately *clean* example
   (a lock released in `finally`, a copy-then-iterate, a query with `LIMIT`)
   and confirm the rule stays silent. `test_concurrency_rules.py` already
   does this per new rule — mirror the pattern for rules you add.
3. **Proof test**: run the full `audit` skill (map → trace → queue →
   investigate → repro → record → report) against a real bug and check the
   `.audit/repro.json`/`report.md` actually show a passing reproduction with
   real measured numbers (loop lag, attempt counts, row counts) — not just a
   verdict string.
4. **Adversarial test**: feed it the wrong path on purpose (trailing space,
   near-duplicate sibling dir) and confirm `map`/`queue` emit a
   `path_warnings`/`warning:` line rather than silently misreading it.
5. **Budget test**: plant more functions-with-bugs than the default budget
   (15) covers and confirm the queue ranks by severity/confidence and defers
   the rest rather than investigating everything shallowly.

## Where to look when something doesn't match this doc

- `plugins/argus/scripts/auditor/langs/patterns.py` — all regex pattern rules.
- `plugins/argus/scripts/auditor/langs/python.py` — Python-specific hooks
  (locks, races, mutation, threads/fanout, uncommitted writes).
- `plugins/argus/scripts/auditor/effects.py` — cross-call-graph propagation
  (deadlines, retries, waits).
- `plugins/argus/scripts/auditor/analysis.py` — where findings get assembled
  and severities assigned, including the lock-order-inversion pass.
- `plugins/argus/agents/investigator.md` — exactly how a lead becomes a
  reproduction and a verdict.
- `tests/test_concurrency_rules.py` — the authoritative recall/precision
  tests; `git log -p` on these files shows exactly when each rule landed.

Re-run `python3 plugins/argus/scripts/auditor_cli.py langs` and
`python3 -m pytest -q tests` before trusting any of the above if more commits
have landed since 2026-10-03 — this file goes stale the moment the rules do.
