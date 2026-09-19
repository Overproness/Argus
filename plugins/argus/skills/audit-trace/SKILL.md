---
name: audit-trace
description: >-
  Run a program, test suite or server in any language under argus runtime observation,
  and turn static findings from audit-map into evidence. Python 3.12+ is traced function
  by function automatically. Every language can send OpenTelemetry spans to the argus
  receiver (--otlp; Java, Kotlin and Scala via the Java agent, .NET, Node, Go, Rust, Ruby
  and PHP via their OpenTelemetry SDKs) and have heartbeat gaps in its output turned into
  stalls (--heartbeat). It reports event-loop stalls with their call stack, N+1 fan-out
  counts, observed external calls with latency and errors, recursion depth, O(n^k)
  complexity fits and scaling projections. Use after audit-map when findings need
  evidence, when the user asks "does this actually block / how slow is it / does it
  scale", or to see where time goes.
argument-hint: "[repo path] [--otlp] [--heartbeat REGEX] -- <command that exercises the code>"
---

# Audit trace

`audit-map` produces leads. This skill produces evidence: it runs the code and
reports what happened, joined to each finding. You interpret; do not invent.

## 1. Pick the channel for the repo's language

| Channel | Languages | What it gives | Flag |
|---|---|---|---|
| Built-in tracer | Python 3.12+ | every repo function call, slices, loop stalls with stacks, argument sizes | none (automatic) |
| OpenTelemetry | any language with an OTel agent or SDK | spans mapped to repo functions (by code attributes or names), plus external calls (HTTP, DB, RPC, messaging) with latency and errors | `--otlp` |
| Heartbeat | any program that prints periodically (a tick log, a request log) | stalls: gaps between those lines, attributed to the deepest span covering each gap when spans exist | `--heartbeat REGEX` |

The channels combine. `--otlp --heartbeat "tick"` gives stalls attributed to
functions in any language.

`--otlp` starts a local OTLP/HTTP receiver and sets `OTEL_EXPORTER_OTLP_*` for
the program. It accepts protobuf and JSON. The program must already be
instrumented. Prefer ways that need no code change:

| Language | How to get spans (no code change first) | Spans for the repo's own functions |
|---|---|---|
| Java, Kotlin, Scala | `java -javaagent:opentelemetry-javaagent.jar ...` (from the opentelemetry-java-instrumentation releases) | automatic: `trace --otlp` sets `OTEL_INSTRUMENTATION_METHODS_INCLUDE` to the classes and methods in the map's JVM findings and entry points (set it yourself to override) |
| C# / .NET | OpenTelemetry .NET automatic instrumentation (its install script sets the profiler variables) | HTTP and DB client spans are automatic; functions need an `ActivitySource` in code |
| JavaScript / TypeScript | `node --require @opentelemetry/auto-instrumentations-node/register app.js` | client spans are automatic; functions need manual spans |
| Python | not needed: the built-in tracer records every function. Add `opentelemetry-instrument` for client spans | the built-in tracer |
| Go, Rust, Ruby, PHP, Swift, C/C++ | the OpenTelemetry SDK in code (Rust: `tracing-opentelemetry` if the repo uses `tracing`; Go: the SDK or eBPF auto-instrumentation) | spans the code already creates |

Never add instrumentation code or dependencies to the repo without the user's
explicit consent. If the repo has no OpenTelemetry and the language needs code
for it, use `--heartbeat` alone: program-level stalls are still evidence. Say
that function-level attribution needs spans.

## 2. Pick a workload

Evidence is only as good as the code the workload exercises. In order of preference:

1. The test suite (`-- python -m pytest tests -x -q`, `-- cargo test`,
   `-- mvn -q test`, `-- npm test`, `-- dotnet test`, `-- go test ./...`).
2. A script, CLI or entry point with realistic inputs.
3. A dev server plus a load generator, started in the same command.

Never run against production credentials, live exchanges or shared databases.
If an entry point needs a remote dependency, point it at a fault server
(`auditor_cli.py fault-server`), which gives controlled latency too, or ask the
user for a sandbox. Inputs of several sizes (10, 100, 1000 items) let the
complexity fit work. `--assume ARG=N` projects to a production size.

## 3. Run

```bash
python "<plugin-root>/scripts/auditor_cli.py" map "<repo>"          # if .audit/map.json is missing or stale
python "<plugin-root>/scripts/auditor_cli.py" trace "<repo>" [--otlp] [--heartbeat REGEX] -- <command>
python "<plugin-root>/scripts/auditor_cli.py" trace-import "<repo>" --otlp-file spans.json   # spans recorded elsewhere
```

- The command runs with `<repo>` as working directory. Everything after `--`
  belongs to the command. Python processes it starts are traced through a
  `sitecustomize` on `PYTHONPATH` (a project that ships its own
  `sitecustomize.py` is shadowed).
- `--stall-ms 100` sets what counts as a stall. For heartbeats, a stall is a
  gap this much longer than the usual interval.
- `--no-shapes` skips argument-size capture.
- Output: `.audit/trace/` (raw: `*.db`, `*.spans.jsonl`, `*.heartbeat.jsonl`),
  `.audit/trace.json` and `.audit/trace.md`. `trace-report <repo>` rebuilds the
  report from the raw data, re-mapped against the current map.

## 4. Read the evidence

Open `.audit/trace.md`. Each finding from the map carries a status:

| Status | Meaning | What you say |
|---|---|---|
| confirmed | the predicted effect was observed (a stall on the predicted stack, N+1 fan-out of calls or external calls) | confirmed; quote the number and stack |
| not-observed | the code ran, the effect did not appear | evidence against *for this workload*; say what the workload lacked (slow peer, large input) |
| not-exercised | the function never ran | the workload does not cover it; suggest one that would |
| not-traced | the run had no function-level view of this language (for example, only client spans from auto-instrumentation) | no runtime verdict; name what would give one (method spans, an SDK span, a native tracer) and use the external-call numbers |
| measured | a number the finding asked for (exponent, depth) | report it; an exponent ≥ 2 with R² ≥ 0.9 is a real scaling risk |
| not-verifiable | tracing cannot decide (timeouts, lock semantics) | keep the static verdict; use the attached timings and external-call latencies for magnitude |

Then read:
- **Stalls the map did not predict.** Runtime found blocking the static rules
  missed. Treat each as a new lead: open the code, find the call, and note it
  under "Unmapped observations" so the rules can be extended. A
  `(program-level: …)` stall had no span to attribute it to.
- **External calls observed.** The slowest dependencies, their error counts,
  and which functions called them. A maximum near a library default timeout
  (30 s, 100 s) means the call really waited that long.
- **Scaling projections.** Super-linear functions extrapolated to larger
  inputs. Treat these as orders of magnitude.

## 5. Report

Update the audit-map triage:
- move confirmed findings to **confirmed**, with the observed magnitude;
- downgrade not-observed ones, with the workload caveat.

Do not change the severity of a not-verifiable finding based on speed alone: a
5 ms call without a timeout still hangs forever when the peer does.
