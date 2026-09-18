---
name: audit-trace
description: Run a Python program, test suite or server under the repo-auditor runtime tracer and turn static findings from audit-map into evidence. Records every repo function call with timing, event-loop stalls (blocking calls on async paths, with the call stack), N+1 fan-out counts, recursion depth and O(n^k) complexity fits. Use after audit-map when findings are labelled needs-evidence, when the user asks "does this actually block / how slow is it / does it scale", or to profile where time goes.
argument-hint: "[repo path] -- <command that exercises the code>"
---

# Audit trace

`audit-map` produces leads. This skill produces evidence: it runs the code and
reports what happened, joined to each finding. You interpret; do not invent.

Python 3.12+ only for the traced program. Other languages: not yet (see PLAN.md M2).

## 1. Pick a workload

Evidence is only as good as the code the workload exercises. In order of preference:

1. The test suite: `-- python -m pytest tests -x -q`.
2. A script or CLI entry point with realistic inputs.
3. A dev server plus a load generator, started in the same command
   (`-- sh -c "uvicorn app:api & sleep 1; python load.py; kill %1"`).

Never run against production credentials, live exchanges or shared databases.
If the repo's entry points need them, ask the user for a sandbox or a mock
first. Inputs of several sizes (10, 100, 1000 items) let the complexity fit work.

## 2. Run

```bash
python "<plugin-root>/scripts/auditor_cli.py" map "<repo>"          # if .audit/map.json is missing or stale
python "<plugin-root>/scripts/auditor_cli.py" trace "<repo>" -- <command>
```

- The command runs with `<repo>` as working directory. Every Python process it
  starts (including subprocesses) is traced through a `sitecustomize` on
  `PYTHONPATH`; a project that ships its own `sitecustomize.py` is shadowed.
- `--stall-ms 100` sets what counts as a stall (an uninterrupted stretch of one
  function on a thread running an asyncio loop). Lower it for latency-critical loops.
- `--no-shapes` skips argument-size capture if the target passes huge or exotic objects.
- Output: `.audit/trace/*.db` (raw), `.audit/trace.json`, `.audit/trace.md`.
  `trace-report <repo>` rebuilds the report from existing raw data.

## 3. Read the evidence

Open `.audit/trace.md`. Each finding from the map carries a status:

| Status | Meaning | What you say |
|---|---|---|
| confirmed | the predicted effect was observed (stall on the predicted stack, N+1 fan-out) | confirmed, quote the number and stack |
| not-observed | the code ran, the effect did not appear | evidence against *for this workload*; say what the workload lacked (slow peer, large input) |
| not-exercised | the function never ran | the workload does not cover it; suggest one that would |
| measured | a number the finding asked for (exponent, depth) | report it; an exponent ≥ 2 with R² ≥ 0.9 is a real scaling risk |
| not-verifiable | tracing cannot decide (timeouts, lock semantics) | keep the static verdict; use the attached timings for magnitude |

Then read **Stalls the map did not predict**: runtime found blocking the
static rules missed. Treat each as a new lead: open the code, find the library
call, and note it under "Unmapped observations" so the rules can be extended.

## 4. Report

Update the audit-map triage: move confirmed findings to **confirmed** with
the observed magnitude, and downgrade not-observed ones with the workload
caveat. Do not change severities of not-verifiable findings based on speed
alone: a 5 ms call without a timeout still hangs forever when the peer does.
