# Repo audit map

Root: `'/private/tmp/claude-501/-Users-adyaanahmed-Documents-hello-guys-/b00b1548-7f33-4549-ac88-353566d7102f/scratchpad/argus_target'`

2 files [python (2)] · 8 functions (7 async) · 7 call edges · 10 I/O boundaries · 21 findings {'high': 5, 'medium': 16}

_Call resolution: 0 files precise (SCIP), 2 name-based. Findings are leads, not verdicts: each needs evidence before it counts._

## Findings

- **[high] blocking-in-async**: `app.health` at `app/main.py:34` (python, exact)  
  Blocking call on an async path (blocking sleep `time.sleep(0.05)`). A slow response stalls the asyncio event loop, freezing every coroutine.
- **[high] blocking-in-async**: `app.process_job` at `app/main.py:52` (python, exact)  
  Blocking call on an async path (blocking sleep `time.sleep(0.2)`). A slow response stalls the asyncio event loop, freezing every coroutine.
  - reached from: app.create_job
- **[high] unbounded-wait-loop**: `app.get_job` at `app/main.py:65` (python, heuristic)  
  Polls a condition with no iteration cap and no deadline (no `time.time()`/`monotonic()` check against an elapsed bound). If the condition never becomes true, this loop runs forever, holding the request open indefinitely. Add a deadline (compare elapsed time to a max) or a maximum number of iterations.
- **[high] race-across-await**: `app.fetch_remote` at `app/main.py:74` (python, heuristic)  
  Shared `CACHE` is read here and written at line 81, with an `await` in between. Other requests and tasks run during the await and change it, so this write loses their update or acts on a stale check (lost update, check-then-act, double spend). Hold one asyncio.Lock across the read and the write, or remove the await between them.
- **[high] blocking-in-async**: `app.run_batch` at `app/main.py:89` (python, heuristic)  
  Blocking call on an async path (blocking future `results = [future.result() for future in futures]`). A slow response stalls the asyncio event loop, freezing every coroutine.
- **[medium] unbounded-module-container**: `app.record_request` at `app/main.py:28` (python, heuristic)  
  Module-level `REQUEST_COUNTS` is only ever added to across this file, never popped, removed or cleared. Every request or background cycle that adds to it grows the process's memory a little more, for as long as the process runs. It is never read back by key either; check it is still needed at all. Evict old entries (an LRU/TTL cache, a max size, or a cleanup pass) or move it out of module scope.
- **[medium] blocking-in-async**: `app.create_job` at `app/main.py:41` (python, heuristic)  
  Blocking call on an async path (blocking db `cursor = DB.execute("insert into jobs (name, status) values (?, ?)", (name, "queued"))`). A slow response stalls the asyncio event loop, freezing every coroutine.
- **[medium] blocking-in-async**: `app.create_job` at `app/main.py:42` (python, heuristic)  
  Blocking call on an async path (blocking db `DB.commit()`). A slow response stalls the asyncio event loop, freezing every coroutine.
- **[medium] unbounded-module-container**: `app.create_job` at `app/main.py:45` (python, heuristic)  
  Module-level `WORK_QUEUE` is only ever added to across this file, never popped, removed or cleared. Every request or background cycle that adds to it grows the process's memory a little more, for as long as the process runs. It is never read back by key either; check it is still needed at all. Evict old entries (an LRU/TTL cache, a max size, or a cleanup pass) or move it out of module scope.
- **[medium] fire-and-forget-task**: `app.create_job` at `app/main.py:46` (python, heuristic)  
  A spawned task whose handle is dropped. Nothing waits for it, so its errors and panics vanish, shutdown does not wait for it, and (Python) it can be garbage collected mid-run.
- **[medium] blocking-in-async**: `app.process_job` at `app/main.py:53` (python, heuristic)  
  Blocking call on an async path (blocking db `DB.execute("update jobs set status = ? where id = ?", ("done", job_id))`). A slow response stalls the asyncio event loop, freezing every coroutine.
  - reached from: app.create_job
- **[medium] blocking-in-async**: `app.process_job` at `app/main.py:54` (python, heuristic)  
  Blocking call on an async path (blocking db `DB.commit()`). A slow response stalls the asyncio event loop, freezing every coroutine.
  - reached from: app.create_job
- **[medium] unbounded-module-container**: `app.process_job` at `app/main.py:55` (python, heuristic)  
  Module-level `EVENTS` is only ever added to across this file, never popped, removed or cleared. Every request or background cycle that adds to it grows the process's memory a little more, for as long as the process runs. It is never read back by key either; check it is still needed at all. Evict old entries (an LRU/TTL cache, a max size, or a cleanup pass) or move it out of module scope.
- **[medium] blocking-in-async**: `app.get_job` at `app/main.py:61` (python, heuristic)  
  Blocking call on an async path (blocking db `row = DB.execute("select id, name, status from jobs where id = ?", (job_id,)).fetchone()`). A slow response stalls the asyncio event loop, freezing every coroutine.
- **[medium] blocking-in-async**: `app.get_job` at `app/main.py:67` (python, heuristic)  
  Blocking call on an async path (blocking db `row = DB.execute("select id, name, status from jobs where id = ?", (job_id,)).fetchone()`). A slow response stalls the asyncio event loop, freezing every coroutine.
- **[medium] io-in-loop**: `app.get_job` at `app/main.py:67` (python, heuristic)  
  db I/O inside a loop (blocking db `row = DB.execute("select id, name, status from jobs where id = ?", (job_id,)).fetchone()`). Cost grows with collection size (N+1 pattern); batch, cache or bound concurrency.
- **[medium] client-per-call**: `app.fetch_remote` at `app/main.py:78` (python, heuristic)  
  An HTTP client/session is constructed inside this function. If this function runs per request (or per loop iteration), it opens a brand-new connection pool every time instead of reusing one: slower (a new TCP/TLS handshake each call), and under load it can exhaust sockets. Build the client once (module scope, or app startup) and reuse it.
- **[medium] unsanitized-url-query**: `app.fetch_remote` at `app/main.py:79` (python, heuristic)  
  A value is spliced directly into a URL's query string (string concat or an f-string/template literal) instead of going through `params=`/a query builder. A `&`, `#`, `%` or newline in the value changes the request's meaning (an extra parameter, a truncated path, header or log injection) and anything the server doesn't expect breaks the request. Pass it as `params=` (requests/httpx) or a URL-encoding helper so it is percent-encoded.
- **[medium] unchecked-response**: `app.fetch_remote` at `app/main.py:80` (python, heuristic)  
  `response` is read here with no status check (`raise_for_status()` / `.status_code` / `.ok`) between the request and this use. An error response (4xx/5xx, or a non-JSON body) is parsed or cached as if it were a success, and the failure surfaces later, far from its cause. Check the status before using the body.
- **[medium] unbounded-module-container**: `app.fetch_remote` at `app/main.py:81` (python, heuristic)  
  Module-level `CACHE` is only ever added to across this file, never popped, removed or cleared. Every request or background cycle that adds to it grows the process's memory a little more, for as long as the process runs. Evict old entries (an LRU/TTL cache, a max size, or a cleanup pass) or move it out of module scope.
- **[medium] hang-reaches-entry**: `app.run_batch` at `app/main.py:86` (python, heuristic)  
  `app.run_batch` can wait forever: app.run_batch → future @ app/main.py:89 (no timeout). No timeout or deadline bounds this path, so one unresponsive peer freezes this entry point.
  - chain: app.run_batch → future @ app/main.py:89 (no timeout)

## I/O boundary inventory

| Function | Location | Kind | Blocking | Context | Timeout | Loop | Conf |
|---|---|---|---|---|---|---|---|
| `app.health` | app/main.py:34 | sleep (sleep) | yes | async | n/a | 0 | exact |
| `app.create_job` | app/main.py:41 | db (db) | yes | async | n/a | 0 | heuristic |
| `app.create_job` | app/main.py:42 | db (db) | yes | async | n/a | 0 | heuristic |
| `app.process_job` | app/main.py:52 | sleep (sleep) | yes | async | n/a | 0 | exact |
| `app.process_job` | app/main.py:53 | db (db) | yes | async | n/a | 0 | heuristic |
| `app.process_job` | app/main.py:54 | db (db) | yes | async | n/a | 0 | heuristic |
| `app.get_job` | app/main.py:61 | db (db) | yes | async | n/a | 0 | heuristic |
| `app.get_job` | app/main.py:67 | db (db) | yes | async | n/a | 1 | heuristic |
| `app.fetch_remote` | app/main.py:79 | http (net) | no | async | yes | 0 | heuristic |
| `app.run_batch` | app/main.py:89 | future (wait) | yes | async | n/a | 1 | heuristic |

## Effects across the call graph

_Static upper bounds: timeouts, library defaults and retry counts multiplied along each path._

**Entry points**: longest wait one activation can hit

| Entry | Location | Worst wait | How | Max attempts per request |
|---|---|---|---|---|
| `app.health` | app/main.py:32 | 50 ms | sleep 50 ms via sleep @ app/main.py:34 (sleep 50 ms) | ∞ |
| `app.create_job` | app/main.py:39 | 5 s | library default 5 s via db @ app/main.py:41 (library default 5 s) | 1 |
| `app.get_job` | app/main.py:59 | 5 s | library default 5 s via db @ app/main.py:61 (library default 5 s) | 1 |
| `app.fetch_remote` | app/main.py:72 | 10 s | timeout 10 s via http @ app/main.py:79 (timeout 10 s) | 1 |
| `app.run_batch` | app/main.py:86 | unbounded | no timeout via future @ app/main.py:89 (no timeout) | ∞ |
| `app.stats` | app/main.py:94 | none |  via  | ∞ |

## Hotspots

| Score | Function | Location | Loop nesting | Fan-in | Async |
|---|---|---|---|---|---|
| 14.5 | `app.process_job` | app/main.py:50 | 0 | 1 | yes |
| 14.0 | `app.fetch_remote` | app/main.py:72 | 0 | 0 | yes |
| 13.0 | `app.get_job` | app/main.py:59 | 1 | 0 | yes |
| 10.0 | `app.create_job` | app/main.py:39 | 0 | 0 | yes |
| 8.0 | `app.run_batch` | app/main.py:86 | 1 | 0 | yes |
| 6.0 | `app.health` | app/main.py:32 | 0 | 0 | yes |
| 5.0 | `app.record_request` | app/main.py:27 | 0 | 6 | no |
