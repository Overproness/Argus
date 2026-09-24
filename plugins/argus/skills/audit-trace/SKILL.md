---
name: audit-trace
description: >-
  Run a program, test suite or server in Rust, Python, JS/TS, Go, Java or C/C++ under argus
  runtime observation, and turn static findings from audit-map into evidence. Python 3.12+ is
  traced function by function automatically, and Node (JavaScript, TypeScript) is profiled
  automatically (V8 sampling profile plus exact call counts). Every language can send
  OpenTelemetry spans to the argus receiver (--otlp; Java via the Java agent, Node, Go and
  Rust via their OpenTelemetry SDKs), have heartbeat gaps in its output turned
  into stalls (--heartbeat), or import a profile recorded elsewhere (trace-import: V8
  .cpuprofile, speedscope from py-spy, or a Rust tracing-chrome trace). It reports event-loop stalls
  with their call stack, N+1 fan-out counts, observed external calls with latency and
  errors, recursion depth, O(n^k) complexity fits and scaling projections. Use after
  audit-map when findings need evidence, when the user asks "does this actually block / how
  slow is it / does it scale", or to see where time goes.
argument-hint: "[repo path] [--otlp] [--heartbeat REGEX] -- <command that exercises the code>"
---

# Audit trace

`audit-map` produces leads. This skill produces evidence: it runs the code and
reports what happened, joined to each finding. You interpret; do not invent.

## 1. Pick the channel for the repo's language

| Channel | Languages | What it gives | Flag |
|---|---|---|---|
| Built-in tracer | Python 3.12+ | every repo function call, slices, loop stalls with stacks, argument sizes | none (automatic) |
| Node profiler | JavaScript, TypeScript on Node | event-loop stalls with the stack and the blocking call (`spawnSync (sampled leaf)`), sampled durations, exact call counts from V8 coverage | none (automatic for `node`, `npm`, `npx`, `yarn`, `pnpm`, `tsx`, `ts-node`); `--node` forces it, `--no-node` turns it off |
| OpenTelemetry | any language with an OTel agent or SDK | spans mapped to repo functions (by code attributes or names), plus external calls (HTTP, DB, RPC, messaging) with latency and errors | `--otlp` |
| Heartbeat | any program that prints periodically (a tick log, a request log) | stalls: gaps between those lines, attributed to the deepest span covering each gap when spans exist | `--heartbeat REGEX` |
| Imported profile | anything that writes a V8 `.cpuprofile` (Chrome or Deno DevTools) or speedscope JSON (`py-spy record -f speedscope --idle`) | sampled durations; stalls only when the profile has idle samples; exact durations from evented speedscope profiles | `trace-import --profile FILE` |
| Rust chrome trace | a `tracing-chrome` recording (one span pair per poll) | per-poll self time; a long poll on an async function's stack is a stall | `trace-import --chrome FILE` |

The channels combine. `--otlp --heartbeat "tick"` gives stalls attributed to
functions in any language. On Node, the profiler runs alongside `--otlp` and
`--heartbeat`: a heartbeat gap that a profile stall explains is reported once,
under the function the profile names.

Sampled data has limits. Durations come from samples every 0.5 ms, so calls
shorter than that can be missed. Call counts are exact only where coverage
recorded them, which the Node channel does. For N+1 findings the evidence is a
ratio of counts ("`get` ran 30× while `main` ran 1×"), which assumes those
calls came from that caller. Recursion depth and complexity fits need exact
activations, so sampled data reports them as not-verifiable.

`--otlp` starts a local OTLP/HTTP receiver and sets `OTEL_EXPORTER_OTLP_*` for
the program. It accepts protobuf and JSON. The program must already be
instrumented. Prefer ways that need no code change:

| Language | How to get spans (no code change first) | Spans for the repo's own functions |
|---|---|---|
| Java | `java -javaagent:opentelemetry-javaagent.jar ...` (from the opentelemetry-java-instrumentation releases) | automatic: `trace --otlp` sets `OTEL_INSTRUMENTATION_METHODS_INCLUDE` to the classes and methods in the map's JVM findings and entry points (set it yourself to override) |
| JavaScript / TypeScript | `node --require @opentelemetry/auto-instrumentations-node/register app.js` | client spans are automatic; functions come from the Node profiler |
| Python | not needed: the built-in tracer records every function. Add `opentelemetry-instrument` for client spans | the built-in tracer |
| Go, Rust, C/C++ | the OpenTelemetry SDK in code (Rust: `tracing-opentelemetry` if the repo uses `tracing`; Go: the SDK or eBPF auto-instrumentation) | spans the code already creates |

Never add instrumentation code or dependencies to the repo without the user's
explicit consent. If the repo has no OpenTelemetry and the language needs code
for it, use `--heartbeat` alone: program-level stalls are still evidence. Say
that function-level attribution needs spans.

### Rust: import a `tracing-chrome` trace

Add to the program, keeping the guard alive until exit, and put `#[tracing::instrument]` on the suspect fns
(async fns included, since each poll becomes one slice):

```rust
let (chrome, _guard) = tracing_chrome::ChromeLayerBuilder::new().include_args(true).build();
tracing_subscriber::registry().with(chrome).init();
```

Run the workload, then `trace-import <repo> --chrome trace-*.json` (MCP: `audit_trace_import` with
`chrome_traces`). A poll with long self time while an async fn is on its stack is a stall, so a sync call
blocking the runtime is confirmed with its stack. Only instrumented functions are seen. N+1 counts for async
callees count polls and can overstate: check them against the code.

## 2. Pick a workload

Evidence is only as good as the code the workload exercises. In order of preference:

1. The test suite (`-- python -m pytest tests -x -q`, `-- cargo test`,
   `-- mvn -q test`, `-- npm test`, `-- go test ./...`).
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
# recorded elsewhere (CI, staging, another machine):
python "<plugin-root>/scripts/auditor_cli.py" trace-import "<repo>" [--otlp-file spans.json] \
    [--profile app.cpuprofile | --profile profile.speedscope.json] [--coverage coverage-dir]
```

- The command runs with `<repo>` as working directory. Everything after `--`
  belongs to the command. Python processes it starts are traced through a
  `sitecustomize` on `PYTHONPATH` (a project that ships its own
  `sitecustomize.py` is shadowed). Node processes it starts are profiled
  through `NODE_OPTIONS` and `NODE_V8_COVERAGE`, child processes included.
- `--stall-ms 100` sets what counts as a stall. For heartbeats, a stall is a
  gap this much longer than the usual interval.
- `--no-shapes` skips argument-size capture.
- Runs accumulate: each `trace` or `trace-import` adds to `.audit/trace/` and
  the report covers all of them. Delete `.audit/trace/` to start over, for
  example before comparing two workloads.
- Output: `.audit/trace/` (raw: `*.db`, `*.spans.jsonl`, `*.heartbeat.jsonl`,
  `node-prof/`, `node-cov/`, `profiles/`, `coverage/`), `.audit/trace.json`
  and `.audit/trace.md`. `trace-report <repo> [--stall-ms N]` rebuilds the
  report from the raw data, re-mapped against the current map. Profiles are
  re-read, so a new threshold applies to them.

## 4. Read the evidence

Open `.audit/trace.md`. Each finding from the map carries a status:

| Status | Meaning | What you say |
|---|---|---|
| confirmed | the predicted effect was observed (a stall on the predicted stack, N+1 fan-out of calls or external calls) | confirmed; quote the number and stack |
| not-observed | the code ran, the effect did not appear | evidence against *for this workload*; say what the workload lacked (slow peer, large input) |
| not-exercised | the function never ran | the workload does not cover it; suggest one that would |
| not-traced | the run had no function-level view of this language (for example, only client spans from auto-instrumentation) | no runtime verdict; name what would give one (method spans, an SDK span, a native tracer) and use the external-call numbers |
| measured | a number the finding asked for (exponent, depth) | report it; an exponent ≥ 2 with R² ≥ 0.9 is a real scaling risk |
| not-verifiable | tracing cannot decide (timeouts, lock semantics), or sampled data cannot (recursion depth, fan-out without counts, stalls in a profile without idle samples) | keep the static verdict; use the attached timings and external-call latencies for magnitude |

Then read:
- **Stalls the map did not predict.** Runtime found blocking the static rules
  missed. Treat each as a new lead: open the code, find the call, and note it
  under "Unmapped observations" so the rules can be extended. A
  `(program-level: …)` stall had no span or repo function to attribute it to.
  A `(sampled leaf)` at the end of a stack is the function the samples were
  in, usually the blocking call itself.
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
