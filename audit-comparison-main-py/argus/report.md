# Audit report: argus_target

_Generated 2026-10-14T12:46:53+00:00 · languages: python (2) · trace: yes · reproductions: yes · 15 investigated over 1 round(s)_

| Status | Count | Meaning |
|---|---|---|
| proven | 10 | a reproduction triggered the predicted effect, and it still passes |
| observed | 6 | the runtime trace showed the effect, or measured the number the finding asked for |
| rejected | 5 | an investigator showed the static claim is wrong |

## Proven (10)

- **[high] unbounded-wait-loop**: `app.get_job` at `app/main.py:65`
  Polls a condition with no iteration cap and no deadline (no `time.time()`/`monotonic()` check against an elapsed bound). If the condition never becomes true, this loop runs forever, holding the request open indefinitely. Add a deadline (compare elapsed time to a max) or a maximum number of iterations.
  - runtime: **not-exercised**: never ran under this workload
  - verdict: **confirmed**; If process_job crashes or is never scheduled, every GET /jobs/{id} for that job hangs forever, polling the DB every 10 ms.
  - fix: Add a max-wait deadline (time.monotonic()) and return 202/504 or the current status when exceeded.
  - reproduction: `.audit/repros/test_get_job.py` (passed)
- **[high] race-across-await**: `app.fetch_remote` at `app/main.py:74`
  Shared `CACHE` is read here and written at line 81, with an `await` in between. Other requests and tasks run during the await and change it, so this write loses their update or acts on a stale check (lost update, check-then-act, double spend). Hold one asyncio.Lock across the read and the write, or remove the await between them.
  - runtime: **not-exercised**: never ran under this workload
  - verdict: **confirmed**; A burst of N requests for one uncached/expired key makes N upstream fetches instead of 1.
  - fix: Per-key asyncio.Lock or shared in-flight future; re-check cache after acquiring.
  - reproduction: `.audit/repros/test_fetch_remote.py` (passed)
- **[high] blocking-in-async**: `app.run_batch` at `app/main.py:89`
  Blocking call on an async path (blocking future `results = [future.result() for future in futures]`). A slow response stalls the asyncio event loop, freezing every coroutine.
  - runtime: **not-exercised**: never ran under this workload
  - verdict: **confirmed**; A single large POST /batch (~200k items) freezes the event loop for ~2.3 s; 10 items stall it ~8 ms.
  - fix: Do not call future.result() on the loop: await asyncio.wrap_future/gather over loop.run_in_executor, or run json.dumps inline.
  - reproduction: `.audit/repros/test_run_batch.py` (passed)
- **[medium] unbounded-module-container**: `app.create_job` at `app/main.py:45`
  Module-level `WORK_QUEUE` is only ever added to across this file, never popped, removed or cleared. Every request or background cycle that adds to it grows the process's memory a little more, for as long as the process runs. It is never read back by key either; check it is still needed at all. Evict old entries (an LRU/TTL cache, a max size, or a cleanup pass) or move it out of module scope.
  - runtime: **not-verifiable**: tracing cannot decide `unbounded-module-container`; ran 1×, max 156 ms; 1 stall(s) passed through this function
  - verdict: **confirmed**; Every POST /jobs appends permanently; memory and /stats payload grow linearly.
  - fix: Remove WORK_QUEUE or bound it with deque(maxlen) / pop when process_job finishes.
  - reproduction: `.audit/repros/test_create_job.py` (passed)
- **[medium] fire-and-forget-task**: `app.create_job` at `app/main.py:46`
  A spawned task whose handle is dropped. Nothing waits for it, so its errors and panics vanish, shutdown does not wait for it, and (Python) it can be garbage collected mid-run.
  - runtime: **not-verifiable**: tracing cannot decide `fire-and-forget-task`; ran 1×, max 156 ms; 1 stall(s) passed through this function
  - verdict: **confirmed**; Job row stays queued forever and GET /jobs/{id} polls forever; only trace is a loop log line.
  - fix: Keep task refs in a set with add_done_callback(discard) and a callback that logs and marks job failed.
  - reproduction: `.audit/repros/test_create_job.py` (passed)
- **[medium] unbounded-module-container**: `app.process_job` at `app/main.py:55`
  Module-level `EVENTS` is only ever added to across this file, never popped, removed or cleared. Every request or background cycle that adds to it grows the process's memory a little more, for as long as the process runs. It is never read back by key either; check it is still needed at all. Evict old entries (an LRU/TTL cache, a max size, or a cleanup pass) or move it out of module scope.
  - runtime: **not-verifiable**: tracing cannot decide `unbounded-module-container`; ran 1×, max 151 ms; 1 stall(s) passed through this function
  - verdict: **confirmed**; Every job permanently appends a dict to EVENTS; memory grows linearly and /stats serialises the whole list.
  - fix: Bound EVENTS with deque(maxlen=N) or drop it in favour of DB job status.
  - reproduction: `.audit/repros/test_process_job.py` (passed)
- **[medium] client-per-call**: `app.fetch_remote` at `app/main.py:78`
  An HTTP client/session is constructed inside this function. If this function runs per request (or per loop iteration), it opens a brand-new connection pool every time instead of reusing one: slower (a new TCP/TLS handshake each call), and under load it can exhaust sockets. Build the client once (module scope, or app startup) and reuse it.
  - runtime: **not-exercised**: never ran under this workload
  - verdict: **confirmed**; Every miss opens a new connection pool and TLS handshake.
  - fix: One module-level or lifespan-managed httpx.AsyncClient.
  - reproduction: `.audit/repros/test_fetch_remote.py` (passed)
- **[medium] unsanitized-url-query**: `app.fetch_remote` at `app/main.py:79`
  A value is spliced directly into a URL's query string (string concat or an f-string/template literal) instead of going through `params=`/a query builder. A `&`, `#`, `%` or newline in the value changes the request's meaning (an extra parameter, a truncated path, header or log injection) and anything the server doesn't expect breaks the request. Pass it as `params=` (requests/httpx) or a URL-encoding helper so it is percent-encoded.
  - runtime: **not-exercised**: never ran under this workload
  - verdict: **confirmed**; Caller can inject extra upstream query parameters.
  - fix: Pass params={'key': key} to client.get.
  - reproduction: `.audit/repros/test_fetch_remote.py` (passed)
- **[medium] unchecked-response**: `app.fetch_remote` at `app/main.py:80`
  `response` is read here with no status check (`raise_for_status()` / `.status_code` / `.ok`) between the request and this use. An error response (4xx/5xx, or a non-JSON body) is parsed or cached as if it were a success, and the failure surfaces later, far from its cause. Check the status before using the body.
  - runtime: **not-exercised**: never ran under this workload
  - verdict: **confirmed**; Transient 5xx served as 200 and cached 60 s.
  - fix: response.raise_for_status() and map to 502 before caching.
  - reproduction: `.audit/repros/test_fetch_remote.py` (passed)
- **[medium] unbounded-module-container**: `app.fetch_remote` at `app/main.py:81`
  Module-level `CACHE` is only ever added to across this file, never popped, removed or cleared. Every request or background cycle that adds to it grows the process's memory a little more, for as long as the process runs. Evict old entries (an LRU/TTL cache, a max size, or a cleanup pass) or move it out of module scope.
  - runtime: **not-exercised**: never ran under this workload
  - verdict: **confirmed**; Unique-key stream grows memory without bound.
  - fix: Bounded TTL/LRU cache or evict expired + cap size.
  - reproduction: `.audit/repros/test_fetch_remote.py` (passed)

## Observed (6)

- **[medium] blocking-in-async**: `app.health` at `app/main.py:34`
  Blocking call on an async path (blocking sleep `time.sleep(0.05)`). A slow response stalls the asyncio event loop, freezing every coroutine.
  - runtime: **blocks if slow** — latency was injected to trigger this; it is what happens *if* the dependency is slow, not evidence it already is: 1 stall(s) on the predicted stack with 150 ms of latency injected at `app.health`, worst 208 ms (the event loop was blocked): health
  - not investigated: trace: confirmed at run time (1 stall(s) on the predicted stack with 150 ms of latency injected at `app.health`, worst 208 ms (the event loop was bloc); no investigation needed
- **[medium] blocking-in-async**: `app.process_job` at `app/main.py:52`
  Blocking call on an async path (blocking sleep `time.sleep(0.2)`). A slow response stalls the asyncio event loop, freezing every coroutine.
  - runtime: **blocks if slow** — latency was injected to trigger this; it is what happens *if* the dependency is slow, not evidence it already is: 1 stall(s) on the predicted stack with 150 ms of latency injected at `app.process_job`, worst 151 ms (the event loop was blocked): process_job
  - not investigated: trace: confirmed at run time (1 stall(s) on the predicted stack with 150 ms of latency injected at `app.process_job`, worst 151 ms (the event loop was); no investigation needed
- **[low] blocking-in-async**: `app.create_job` at `app/main.py:41`
  Blocking call on an async path (blocking db `cursor = DB.execute("insert into jobs (name, status) values (?, ?)", (name, "queued"))`). A slow response stalls the asyncio event loop, freezing every coroutine.
  - runtime: **blocks if slow** — latency was injected to trigger this; it is what happens *if* the dependency is slow, not evidence it already is: 1 stall(s) on the predicted stack with 150 ms of latency injected at `app.create_job`, worst 155 ms (the event loop was blocked): create_job
  - not investigated: trace: confirmed at run time (1 stall(s) on the predicted stack with 150 ms of latency injected at `app.create_job`, worst 155 ms (the event loop was ); no investigation needed
- **[low] blocking-in-async**: `app.create_job` at `app/main.py:42`
  Blocking call on an async path (blocking db `DB.commit()`). A slow response stalls the asyncio event loop, freezing every coroutine.
  - runtime: **blocks if slow** — latency was injected to trigger this; it is what happens *if* the dependency is slow, not evidence it already is: 1 stall(s) on the predicted stack with 150 ms of latency injected at `app.create_job`, worst 155 ms (the event loop was blocked): create_job
  - not investigated: trace: confirmed at run time (1 stall(s) on the predicted stack with 150 ms of latency injected at `app.create_job`, worst 155 ms (the event loop was ); no investigation needed
- **[low] blocking-in-async**: `app.process_job` at `app/main.py:53`
  Blocking call on an async path (blocking db `DB.execute("update jobs set status = ? where id = ?", ("done", job_id))`). A slow response stalls the asyncio event loop, freezing every coroutine.
  - runtime: **blocks if slow** — latency was injected to trigger this; it is what happens *if* the dependency is slow, not evidence it already is: 1 stall(s) on the predicted stack with 150 ms of latency injected at `app.process_job`, worst 151 ms (the event loop was blocked): process_job
  - not investigated: trace: confirmed at run time (1 stall(s) on the predicted stack with 150 ms of latency injected at `app.process_job`, worst 151 ms (the event loop was); no investigation needed
- **[low] blocking-in-async**: `app.process_job` at `app/main.py:54`
  Blocking call on an async path (blocking db `DB.commit()`). A slow response stalls the asyncio event loop, freezing every coroutine.
  - runtime: **blocks if slow** — latency was injected to trigger this; it is what happens *if* the dependency is slow, not evidence it already is: 1 stall(s) on the predicted stack with 150 ms of latency injected at `app.process_job`, worst 151 ms (the event loop was blocked): process_job
  - not investigated: trace: confirmed at run time (1 stall(s) on the predicted stack with 150 ms of latency injected at `app.process_job`, worst 151 ms (the event loop was); no investigation needed

## Rejected (5)

- **[medium] unbounded-module-container**: `app.record_request` at `app/main.py:28`
  Module-level `REQUEST_COUNTS` is only ever added to across this file, never popped, removed or cleared. Every request or background cycle that adds to it grows the process's memory a little more, for as long as the process runs. It is never read back by key either; check it is still needed at all. Evict old entries (an LRU/TTL cache, a max size, or a cleanup pass) or move it out of module scope.
  - runtime: **not-verifiable**: tracing cannot decide `unbounded-module-container`; ran 2×, max 0 ms
  - verdict: **rejected**; None: per-route counter capped at 6 keys.
- **[medium] blocking-in-async**: `app.get_job` at `app/main.py:61`
  Blocking call on an async path (blocking db `row = DB.execute("select id, name, status from jobs where id = ?", (job_id,)).fetchone()`). A slow response stalls the asyncio event loop, freezing every coroutine.
  - runtime: **not-exercised**: never ran under this workload
  - verdict: **rejected**; None: PK lookup on in-memory SQLite takes microseconds.
  - reproduction: `.audit/repros/test_get_job.py` (failed)
- **[medium] blocking-in-async**: `app.get_job` at `app/main.py:67`
  Blocking call on an async path (blocking db `row = DB.execute("select id, name, status from jobs where id = ?", (job_id,)).fetchone()`). A slow response stalls the asyncio event loop, freezing every coroutine.
  - runtime: **not-exercised**: never ran under this workload
  - verdict: **rejected**; None: PK lookup on in-memory SQLite takes microseconds.
  - reproduction: `.audit/repros/test_get_job.py` (failed)
- **[medium] io-in-loop**: `app.get_job` at `app/main.py:67`
  db I/O inside a loop (blocking db `row = DB.execute("select id, name, status from jobs where id = ?", (job_id,)).fetchone()`). Cost grows with collection size (N+1 pattern); batch, cache or bound concurrency.
  - runtime: **not-exercised**: never ran under this workload
  - verdict: **rejected**; None beyond unbounded-wait-loop.
  - reproduction: `.audit/repros/test_get_job.py` (failed)
- **[medium] hang-reaches-entry**: `app.run_batch` at `app/main.py:86`
  `app.run_batch` can wait forever: app.run_batch → future @ app/main.py:89 (no timeout). No timeout or deadline bounds this path, so one unresponsive peer freezes this entry point.
  - chain: app.run_batch → future @ app/main.py:89 (no timeout)
  - runtime: **not-exercised**: never ran under this workload
  - verdict: **rejected**; None: the wait is bounded by CPU work.
  - reproduction: `.audit/repros/test_run_batch.py` (passed)
