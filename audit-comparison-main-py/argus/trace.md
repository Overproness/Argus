# Runtime trace report

1 run(s) [python-tracer] · 7 calls · 0 external calls · 3 stalls (threshold 100 ms) · 4/8 mapped functions exercised

_Command: `-m -q /private/tmp/claude-501/-Users-adyaanahmed-Documents-hello-guys-/b00b1548-7f33-4549-ac88-353566d7102f/scratchpad/argus_target/tests`_

_Evidence is only as good as the workload: **not-observed** means the effect did not appear in this run, **not-exercised** means the code never ran._

## Findings with evidence

Summary: 6 confirmed, 11 not-exercised, 4 not-verifiable

- **[high] blocking-in-async** `app.health` at `app/main.py:34` → **confirmed**: 1 stall(s) on the predicted stack with 150 ms of latency injected at `app.health`, worst 208 ms (the event loop was blocked): health
- **[high] blocking-in-async** `app.process_job` at `app/main.py:52` → **confirmed**: 1 stall(s) on the predicted stack with 150 ms of latency injected at `app.process_job`, worst 151 ms (the event loop was blocked): process_job
- **[high] unbounded-wait-loop** `app.get_job` at `app/main.py:65` → **not-exercised**: never ran under this workload
- **[high] race-across-await** `app.fetch_remote` at `app/main.py:74` → **not-exercised**: never ran under this workload
- **[high] blocking-in-async** `app.run_batch` at `app/main.py:89` → **not-exercised**: never ran under this workload
- **[medium] unbounded-module-container** `app.record_request` at `app/main.py:28` → **not-verifiable**: tracing cannot decide `unbounded-module-container`; ran 2×, max 0 ms
- **[medium] blocking-in-async** `app.create_job` at `app/main.py:41` → **confirmed**: 1 stall(s) on the predicted stack with 150 ms of latency injected at `app.create_job`, worst 155 ms (the event loop was blocked): create_job
- **[medium] blocking-in-async** `app.create_job` at `app/main.py:42` → **confirmed**: 1 stall(s) on the predicted stack with 150 ms of latency injected at `app.create_job`, worst 155 ms (the event loop was blocked): create_job
- **[medium] unbounded-module-container** `app.create_job` at `app/main.py:45` → **not-verifiable**: tracing cannot decide `unbounded-module-container`; ran 1×, max 156 ms; 1 stall(s) passed through this function
- **[medium] fire-and-forget-task** `app.create_job` at `app/main.py:46` → **not-verifiable**: tracing cannot decide `fire-and-forget-task`; ran 1×, max 156 ms; 1 stall(s) passed through this function
- **[medium] blocking-in-async** `app.process_job` at `app/main.py:53` → **confirmed**: 1 stall(s) on the predicted stack with 150 ms of latency injected at `app.process_job`, worst 151 ms (the event loop was blocked): process_job
- **[medium] blocking-in-async** `app.process_job` at `app/main.py:54` → **confirmed**: 1 stall(s) on the predicted stack with 150 ms of latency injected at `app.process_job`, worst 151 ms (the event loop was blocked): process_job
- **[medium] unbounded-module-container** `app.process_job` at `app/main.py:55` → **not-verifiable**: tracing cannot decide `unbounded-module-container`; ran 1×, max 151 ms; 1 stall(s) passed through this function
- **[medium] blocking-in-async** `app.get_job` at `app/main.py:61` → **not-exercised**: never ran under this workload
- **[medium] blocking-in-async** `app.get_job` at `app/main.py:67` → **not-exercised**: never ran under this workload
- **[medium] io-in-loop** `app.get_job` at `app/main.py:67` → **not-exercised**: never ran under this workload
- **[medium] client-per-call** `app.fetch_remote` at `app/main.py:78` → **not-exercised**: never ran under this workload
- **[medium] unsanitized-url-query** `app.fetch_remote` at `app/main.py:79` → **not-exercised**: never ran under this workload
- **[medium] unchecked-response** `app.fetch_remote` at `app/main.py:80` → **not-exercised**: never ran under this workload
- **[medium] unbounded-module-container** `app.fetch_remote` at `app/main.py:81` → **not-exercised**: never ran under this workload
- **[medium] hang-reaches-entry** `app.run_batch` at `app/main.py:86` → **not-exercised**: never ran under this workload

## Stalls the map did not predict

None.


## External calls observed

None recorded (client spans arrive with `--otlp` from an instrumented program).

## Complexity fits (exponent ≥ 1.3)

None fitted (needs ≥4 input sizes spanning 10× and calls over 1 ms).

## Scaling projections

None: no super-linear function has a larger input reaching it (pass `--assume arg=N` to project a size you expect in production).

## Slowest functions by self time

| Function | Location | Calls | Self | Total | p95 | Max slice | Stalls |
|---|---|---|---|---|---|---|---|
| `test_create_job` | tests/test_api.py:15 | 1 | 310 ms | 310 ms | 310 ms | 310 ms | 0 |
| `test_health` | tests/test_api.py:9 | 1 | 212 ms | 212 ms | 212 ms | 212 ms | 0 |
| `app.health` | app/main.py:32 | 1 | 208 ms | 208 ms | 208 ms | 208 ms | 1 |
| `app.create_job` | app/main.py:39 | 1 | 155 ms | 156 ms | 156 ms | 155 ms | 1 |
| `app.process_job` | app/main.py:50 | 1 | 151 ms | 151 ms | 151 ms | 151 ms | 1 |
| `app.record_request` | app/main.py:27 | 2 | 0 ms | 0 ms | 0 ms | 0 ms | 0 |
