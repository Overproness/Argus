# Reproduction results

3 failed, 10 passed

_A passing test means the reproduction triggered the predicted effect. Evidence lines are what the harness measured. Each file ran in its own process under `/private/tmp/claude-501/-Users-adyaanahmed-Documents-hello-guys-/b00b1548-7f33-4549-ac88-353566d7102f/scratchpad/argus_target/.audit/venv/bin/python`._

## test_create_job_grows_without_bound — **passed**

Finding: `unbounded-module-container@app.create_job:45`  
File: `test_create_job.py` · 10.68 s

- before=0, after=50, calls=50

## test_create_job_task_exception_is_lost — **passed**

Finding: `fire-and-forget-task@app.create_job:46`  
File: `test_create_job.py` · 0.59 s

- response={"id": 51, "name": "x", "status": "queued"}, caller_exception="None", loop_handler_messages=["Task exception was never retrieved"]

## test_fetch_remote_loses_updates — **passed**

Finding: `race-across-await@app.fetch_remote:74`  
File: `test_fetch_remote.py` · 0.13 s

- kind="concurrent", fn="fetch_remote", calls=50, errors=0, error_types=[]
- upstream_calls=50, uncached_responses=50, expected=1, cache_entries=1

## test_fetch_remote_client_per_call — **passed**

Finding: `client-per-call@app.fetch_remote:78`  
File: `test_fetch_remote.py` · 0.00 s

- clients_built=20, calls=20

## test_fetch_remote_unsanitized_query — **passed**

Finding: `unsanitized-url-query@app.fetch_remote:79`  
File: `test_fetch_remote.py` · 0.00 s

- raw_url="https://example.com/?key=abc&evil=1", params={"key": ["abc"], "evil": ["1"]}

## test_fetch_remote_unchecked_response — **passed**

Finding: `unchecked-response@app.fetch_remote:80`  
File: `test_fetch_remote.py` · 0.00 s

- first={"key": "k", "value": "not json: upstream exploded", "cached": false}, second={"key": "k", "value": "not json: upstream exploded", "cached": true}, upstream_calls=1, upstream_status=500

## test_fetch_remote_cache_unbounded — **passed**

Finding: `unbounded-module-container@app.fetch_remote:81`  
File: `test_fetch_remote.py` · 0.06 s

- entries_after_n=500, n=500, entries_after_expiry=501, expired_still_held=500

## test_get_job_never_gives_up — **passed**

Finding: `unbounded-wait-loop@app.get_job:65`  
File: `test_get_job.py` · 3.14 s

- kind="deadline", fn="test_get_job_never_gives_up.<locals>.<lambda>", returned=false, elapsed_s=3.0055, deadline_s=3
- returned=false, deadline_s=3

## test_get_job_blocks_loop — **failed**

Finding: `blocking-in-async@app.get_job:61`  
File: `test_get_job.py` · 0.00 s

- kind="loop_lag", max_lag_s=0.0, ticks=0, period_s=0.01
- max_lag_s=0.0, calls=200

```
assert 0.0 >= 0.05
```

## test_get_job_io_in_loop_is_n_plus_one — **failed**

Finding: `io-in-loop@app.get_job:67`  
File: `test_get_job.py` · 0.31 s

- queries=29, distinct_param_sets=1

```
assert 1 > 1
```

## test_process_job_grows_without_bound — **passed**

Finding: `unbounded-module-container@app.process_job:55`  
File: `test_process_job.py` · 0.69 s

- before=0, after=50, calls=50

## test_record_request_grows_without_bound — **failed**

Finding: `unbounded-module-container@app.record_request:28`  
File: `test_record_request.py` · 0.00 s

- before=null, after=null, calls=50

```
TypeError: unsupported operand type(s) for +: 'NoneType' and 'int'
```

## test_run_batch_blocks_the_loop — **passed**

Finding: `blocking-in-async@app.run_batch:89`  
File: `test_run_batch.py` · 2.49 s

- kind="loop_lag", max_lag_s=0.0011, ticks=9, period_s=0.01
- kind="loop_lag", max_lag_s=2.1245, ticks=9, period_s=0.01
- lag_n10_s=0.0010557500011054797, elapsed_n10_s=0.00037816599797224626, lag_n200k_s=2.1245186250036934, elapsed_n200k_s=2.1273587079995195, submitted={"submitted": 200000}
