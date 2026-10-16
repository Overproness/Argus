# Plain-prompt audit of app/main.py

Prompt given to the agent (verbatim, nothing else):

> Audit the FastAPI app in <plain_target>/app/main.py for bugs, reliability problems, performance problems and
> concurrency issues. List every finding with its location, severity, what goes wrong, and a suggested fix.

The agent was a general-purpose subagent on the same model. It read the file only: it did not run or reproduce
anything. Its report follows, unedited. Line numbers refer to main.py.

---

Audit of main.py (101 lines). This was a read-only review; I did not run or reproduce any of these.

## High severity

**1. Blocking `time.sleep(0.2)` inside `process_job` stalls the whole event loop (L52; spawned at L46).**
- `process_job` is a coroutine, so `time.sleep` freezes every request on the worker for 200 ms per job.
- Jobs serialize: 50 queued jobs mean about 10 s during which no endpoint (including `/health`) responds.
- Fix: use `await asyncio.sleep(...)` if it simulates I/O, or `await asyncio.to_thread(blocking_fn)` / `loop.run_in_executor` for real blocking work. Better, make it a CPU/IO worker pool or an external queue.

**2. `GET /jobs/{id}` can spin forever (L65-68).**
- `while row[2] != "done"` has no timeout, no max iterations, and no failed state.
- If `process_job` raises (DB error), is never scheduled, is garbage-collected (see #4), or the process restarts, the status stays "queued" and the request polls forever.
- Each stuck request is a coroutine that runs a SQLite query every 10 ms on the event loop, and it keeps running after the client disconnects.
- Fix: return the current status immediately (`queued` or `done`) and let clients poll. If long-poll is desired, use a per-job `asyncio.Event` or Future with `asyncio.wait_for(..., timeout)`, and add a `failed` status written in a `try/except` in `process_job`.

**3. `/batch` blocks the event loop on thread-pool futures (L88-89).**
- `future.result()` is a synchronous wait inside an `async def`, so the loop is blocked until the 2-worker pool finishes every item.
- Offloading `json.dumps` to a thread is pure overhead, since it is microseconds of work and the caller waits anyway. A large `items` list stalls everything.
- The body size is unbounded.
- Fix: do the work inline (it is trivial), or `await asyncio.gather(*(loop.run_in_executor(POOL, ...)))`. Cap the list length via `Field(max_length=...)` or `Body`.

**4. Fire-and-forget `asyncio.create_task` with no reference kept (L46).**
- The event loop holds only a weak reference to tasks, so a task can be garbage-collected mid-run.
- Exceptions are silently swallowed ("Task exception was never retrieved"), leaving the job stuck "queued", which feeds #2.
- Fix: keep tasks in a set (`tasks.add(t); t.add_done_callback(tasks.discard)`), log failures, and write a failed status. Preferably use FastAPI `BackgroundTasks` or a real queue.

## Medium severity

**5. Unbounded cache keyed by user input (L18, L74-81).**
- Expired entries are never evicted and `key` is attacker-controlled, so `/remote/<random>` grows memory without limit (memory-exhaustion DoS).
- Fix: use a bounded TTL/LRU cache (e.g. `cachetools.TTLCache(maxsize=...)`), and validate or constrain `key` (length, charset).

**6. Unhandled upstream errors and bad data cached (L78-82).**
- `httpx.TimeoutException`, `ConnectError` and similar propagate as 500s.
- There is no `raise_for_status()`, so 4xx/5xx upstream bodies are cached and served as "valid" for 60 s.
- Fix: wrap in `try/except httpx.HTTPError`, return 502/504, call `raise_for_status()`, and cache only successes.

**7. Cache stampede / no request coalescing (L74-81).**
- Concurrent requests for the same cold or expired key each hit upstream.
- Fix: per-key `asyncio.Lock` or in-flight Future map.

**8. Query-string injection in the upstream URL (L79).**
- `f"https://example.com/?key={key}"` does not URL-encode `key`. A value like `a&admin=1` or `a#frag` alters the upstream request. (The host is fixed, so this is not full SSRF.)
- Fix: `client.get("https://example.com/", params={"key": key})`.

**9. Unbounded growth of `EVENTS` and `WORK_QUEUE`, and `/stats` returns all of it (L17, L19, L45, L55, L96-101).**
- `WORK_QUEUE` is appended to but never consumed. `EVENTS` grows per job.
- `/stats` serializes both entire lists on the event loop. Response size and latency grow without bound, and job names are exposed on an unauthenticated endpoint.
- Fix: use `collections.deque(maxlen=N)`, expose counts or the last N only, remove the dead `WORK_QUEUE`, and protect or omit the endpoint.

**10. In-memory SQLite plus module-level state breaks under multiple workers and restarts (L16-24).**
- With `uvicorn --workers N` or multiple replicas, each process has its own `:memory:` DB, cache and counters. A job created on worker A returns 404 on worker B, and `/stats` is inconsistent.
- A restart loses all jobs, and pending tasks are cancelled leaving no "done" state.
- Fix: use a file or server DB (Postgres) and an external queue/cache (Redis), or document and enforce a single worker.

**11. Synchronous SQLite calls on the event loop (L41-42, L53-54, L61, L67).**
- Every query and commit blocks the loop. This is minor with `:memory:` but becomes severe with a file DB.
- A single shared connection with `check_same_thread=False` and no lock is safe today only because everything runs on one thread. It will corrupt or raise "recursive cursor" and interleaved-transaction errors the moment any DB call moves to a thread (e.g. when fixing #1/#3).
- Fix: use `aiosqlite` or per-thread/per-request connections, or wrap all DB calls in `asyncio.to_thread` behind a lock.

## Low severity

**12. `time.sleep(0.05)` in `/health` (L34).**
- It blocks the loop on every health check and degrades liveness probes under load. It is also pointless.
- Fix: remove it, or use `await asyncio.sleep`.

**13. New `httpx.AsyncClient` per request (L78).**
- This causes a TLS handshake and connection setup every time, and no pooling.
- Fix: create a shared client in a FastAPI lifespan handler and close it on shutdown.

**14. Whole upstream body read before slicing to 100 chars (L80).**
- `response.text[:100]` loads the entire response into memory. `timeout=10` is per-phase, not total, so worst case is longer than 10 s.
- Fix: stream and read only the first N bytes, and use a total deadline (`asyncio.timeout`) plus a size cap.

**15. `POST /jobs` takes `name: str` as a query parameter, with no validation (L39).**
- This is likely unintended for a POST, and the name length is unbounded. `cursor.lastrowid` is typed `int | None`, which flows into `process_job`.
- Fix: use a Pydantic body model with `max_length`, and assert `job_id is not None`.

**16. Non-atomic read-modify-write in `record_request` (L27-28).**
- It is safe today because there is no `await` between the get and the set. It becomes a lost-update race if any handler is changed to `def` (threadpool) or the work moves to threads.
- Fix: use `collections.Counter` under a lock, or Prometheus counters. Counts are also per-process (see #10).

**17. Wall-clock time used for TTL (L75, L81).**
- `time.time()` jumps with NTP or clock changes.
- Fix: use `time.monotonic()`.

**18. No lifecycle management (L20-24).**
- `POOL` and `DB` are created at import time and never shut down, and in-flight tasks are not drained on shutdown.
- Fix: use lifespan startup/shutdown to create resources, `POOL.shutdown`, and cancel or await pending tasks.

**19. No backpressure on job creation (L46).**
- Unlimited tasks can be created per request flood, with no concurrency limit (semaphore) or queue bound.

## Summary of priorities
1. Remove blocking calls in async handlers: #1, #3, #12, #11.
2. Fix the infinite wait and lost-task handling: #2, #4.
3. Bound memory growth: #5, #9, #19.
4. Harden the upstream call: #6, #7, #8, #13, #14.
5. Move off in-process state if scaling out: #10.

I did not review `app/__init__.py` (it exists alongside `main.py`) beyond confirming it is present.
`requirements.txt` pins fastapi 0.115.6, uvicorn 0.34.0, httpx 0.28.1 and pytest 8.3.4, and no tests were found.
