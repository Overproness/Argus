# Argus

Claude Code plugin marketplace for agentic code-audit workflows, by the CODEFORGE AI Team. See [PLAN.md](PLAN.md).

## Argus (plugin)

A static audit map for six languages (Rust, Python, JavaScript/TypeScript, Go,
Java, C, C++), plus runtime observation that turns the map's findings into
evidence:
- a built-in function tracer for Python;
- Node's own profiler for JavaScript and TypeScript (a V8 sampling profile plus
  exact call counts from V8 coverage), switched on automatically;
- a Rust `tracing-chrome` trace import (one poll of an instrumented span per
  slice; a long poll on an async function's stack is a stall);
- an OpenTelemetry receiver for everything instrumented (the Java agent, or
  any SDK);
- heartbeat stalls for any program that prints;
- profiles recorded elsewhere: V8 `.cpuprofile`, or speedscope JSON from
  py-spy.

It produces:

- the call graph;
- a list of every I/O and external call (network, database, filesystem, sleeps,
  processes, waits), each marked blocking or not, with its context and timeout
  status;
- findings: blocking calls on async paths (followed through call chains),
  missing timeouts, I/O in loops (N+1), sync locks held across `await`,
  sync-over-async, unbounded goroutines, `defer` in loops, recursion, nested
  loops, and source-pattern leads (unbounded channels, floats for money, wall
  clock used for intervals, sequential awaits, fire-and-forget spawns, panics
  on parsed data, catastrophic-backtracking regexes, unpaginated queries,
  backoff without jitter, CPU-heavy work on an async path);
- hotspots;
- runtime evidence per finding: event-loop stalls with their call stack, N+1
  fan-out counts, recursion depth, O(n^k) complexity fits, and stalls the
  static rules did not predict;
- effects across the call graph (M4): the worst-case wait of every entry
  point, with timeouts, library defaults and retry counts multiplied along each
  path. Findings from this:
  - deadlines that cannot fire because the code under them blocks the thread;
  - inner timeouts and retries that do not fit the caller's deadline;
  - entry points that can wait forever;
  - retries with no backoff, retries with no limit, and retry amplification
    across layers;
  - `unwrap`/`expect` on network results;
  - Go I/O errors discarded with `_`;
- scaling projections: super-linear functions projected to the largest input
  seen upstream, or to a size given with `--assume`;
- linter evidence: SARIF results (clippy, ruff, golangci-lint, eslint,
  semgrep, ...) placed in the map function they fall in; a result that means
  the same as a map finding corroborates it, the rest are leads;
- reproductions in any of the six languages: an investigator subagent writes a
  test per finding that triggers the predicted effect under controlled
  conditions (injected latency, dead peer, simulated outage, scaling inputs),
  runs it, and reports a verdict with numbers:
  - Python code runs in-process;
  - every other language runs against the **fault server** (a local mock API
    or TCP proxy that injects faults and counts connections), either as a
    black-box run of the real program or as a **native probe**: a small
    program in the target language, in a side project that depends on the repo
    by path;
  - probes are verified for Rust, JavaScript, TypeScript, Java, Go, C and C++;
- budgeted rounds (M4): a deterministic queue picks what to reproduce next,
  follows confirmed effects up to their callers, and suppresses findings whose
  call edge an investigator rejected;
- a final report (`report.html`, `report.md`, `report.json`) that ranks every
  finding by how far it got: proven, observed, unverified, inconclusive,
  not observed, rejected.

**Getting started needs nothing but Python 3.12+.** `pip install` is optional:
the first time the CLI or the MCP server runs on a machine without
`tree-sitter` and friends, it builds a private virtualenv under
`~/.cache/argus/venv` (override with `$ARGUS_HOME`), installs the
requirements into it once, and carries on. Expect the very first run to take
about half a minute; every run after that starts instantly. If you would
rather use your own environment, `pip install -r plugins/argus/requirements.txt`
still works and skips all of this.

For **Python, C, C++ and Rust** specifically, everything below works with just
that (plus `gcc`/`g++`/`cargo` on your `PATH` if you want native reproduction
probes for C/C++/Rust; Argus cannot compile your code without them). Python
3.12+ programs are also traced function-by-function with no setup at all.

```bash
python plugins/argus/scripts/auditor_cli.py langs             # supported languages
python plugins/argus/scripts/auditor_cli.py map path/to/repo  # -> path/to/repo/.audit/map.{json,md}
python plugins/argus/scripts/auditor_cli.py index path/to/repo  # optional: SCIP indexes for precise calls
python plugins/argus/scripts/auditor_cli.py trace path/to/repo -- python -m pytest tests
#   -> path/to/repo/.audit/trace.{json,md}; Python 3.12+ is traced function by function
python plugins/argus/scripts/auditor_cli.py trace path/to/repo -- npm test
#   Node: sampling profile + exact call counts, on automatically for node/npm/npx/yarn/pnpm/tsx
python plugins/argus/scripts/auditor_cli.py trace path/to/repo --otlp --heartbeat "tick" -- java -javaagent:otel.jar -jar app.jar
#   any language: OpenTelemetry spans + stalls from gaps between "tick" lines
python plugins/argus/scripts/auditor_cli.py trace-import path/to/repo --otlp-file collector-dump.json
python plugins/argus/scripts/auditor_cli.py trace-import path/to/repo --profile py-spy.speedscope.json
python plugins/argus/scripts/auditor_cli.py trace-import path/to/repo --chrome trace-1234.json   # Rust tracing-chrome
python plugins/argus/scripts/auditor_cli.py lint-import path/to/repo --sarif ruff.sarif   # linter results as evidence
python plugins/argus/scripts/auditor_cli.py trace-report path/to/repo --assume rows=50000  # rebuild, project sizes
#   runs accumulate in .audit/trace/; delete it to start over
python plugins/argus/scripts/auditor_cli.py repro path/to/repo   # run .audit/repros/test_*.py -> .audit/repro.{json,md}
python plugins/argus/scripts/auditor_cli.py queue path/to/repo --budget 10 --per-round 5 --max-rounds 3
python plugins/argus/scripts/auditor_cli.py record path/to/repo --file verdicts.json   # or --file - for stdin
python plugins/argus/scripts/auditor_cli.py report path/to/repo  # -> .audit/report.{json,md,html}
python plugins/argus/scripts/auditor_cli.py fault-server --hang --record conns.jsonl   # point any program at it
python plugins/argus/scripts/auditor_cli.py probe path/to/repo --lang rust --name fetch --build  # native probe side project

# Try the plugin without installing it
claude --plugin-dir plugins/argus
#   /argus:audit path/to/repo --budget 10 [-- <workload>]   everything, in budgeted rounds, ending in the report
#   /argus:audit-map path/to/repo                       static map + triage
#   /argus:audit-trace path/to/repo -- <command>        runtime evidence
#   /argus:audit-investigate path/to/repo               one round of reproductions via the argus:investigator agent
#   MCP tools (any client, see plugins/argus/.mcp.json):
#     audit_map, audit_trace, audit_trace_import, audit_lint_import, run_repro, audit_queue, audit_record, audit_report

# Install it through this marketplace (inside Claude Code)
/plugin marketplace add <path or git URL of this repo>
/plugin install argus@argus
```

Layout of `plugins/argus/scripts/auditor/`:

| File | Role |
|---|---|
| `extract.py` | Generic tree-sitter extraction, driven by a language spec |
| `langs/*.py` | One spec per language: node types, async rules, I/O rules, hooks |
| `langs/patterns.py` | Source-pattern rules shared by every language (one hook, one table of regexes per rule) |
| `analysis.py` | Call resolution, I/O classification, passing effects between functions, findings |
| `effects.py` | Worst-case waits, deadlines, retry multipliers and crash-on-error, propagated across the call graph |
| `timeouts.py` | Durations out of source text: timeout arguments, deadline wrappers, sleeps, library defaults |
| `scip.py` | Precise call resolution from SCIP indexes (no protobuf dependency), plus the indexer runner |
| `linters.py` | SARIF import: results placed in map functions, corroborating equivalent map findings |
| `report.py` | `map.json` and `map.md` output |
| `trace/store.py` | SQLite trace store: one file per traced process, merged on read |
| `trace/py/tracer.py` | Python tracer (`sys.monitoring`): per-call timing in slices, self time, event-loop stalls, argument sizes |
| `trace/run.py` | Runs a command with tracers attached through `AUDIT_TRACE_*` environment variables, Node's profiler and coverage (`NODE_OPTIONS`, `NODE_V8_COVERAGE`), an OTLP receiver (`--otlp`) and heartbeat capture (`--heartbeat`) |
| `trace/otlp.py` | OpenTelemetry in, from any language: OTLP/HTTP receiver (protobuf and JSON, gzip) and file importer |
| `trace/spans.py` | Spans mapped to map functions; client spans as observed external calls; heartbeat gaps as stalls attributed to the deepest covering span |
| `trace/profiles.py` | Sampling profiles (V8 `.cpuprofile`, speedscope) as activations and loop stalls with stacks; V8 coverage as exact call counts; profile clocks aligned with spans and heartbeats |
| `trace/chrome.py` | Chrome trace-event importer (Rust `tracing-chrome`): per-poll slices mapped to repo functions by `.file`/`.line` span fields or by name |
| `trace/sources.py` | Everything under `.audit/trace/` as one trace, and imports of spans, profiles, coverage and chrome traces recorded elsewhere |
| `protowire.py` | Minimal protobuf wire reader shared by the SCIP and OTLP decoders (no protobuf dependency) |
| `procs.py` | Finds programs the way a shell would, so `npm`, `mvn`, `gradle` and other `.cmd`/`.bat` shims run on Windows |
| `trace/fit.py` | Complexity fitting: (input size, duration) samples to O(n^k) |
| `trace/evidence.py` | Joins traces to `map.json`; writes `trace.json` and `trace.md` |
| `repro/harness.py` | Reproduction helpers: `latency`, `hang`, `fail_connect`, `count_connects`, `refuse_remote`, `loop_monitor`, `call_with_deadline`, `scaling`, `count_calls`; each emits `@@evidence` lines |
| `repro/faults.py` | Fault server for any language: a mock HTTP API or TCP proxy with latency, hang, reset and fail-first-N; records every connection |
| `repro/native.py` | `run_target` (run and observe any program: deadline, exit code, heartbeat gaps, `@@evidence` lines, process-tree kill) and native probe scaffolds for Rust, JavaScript, TypeScript, Java, Go, C and C++ |
| `repro/runner.py` | Runs `.audit/repros/test_*.py` under pytest, collects outcomes and evidence into `repro.{json,md}` |
| `orchestrate.py` | The round loop: ranked, budgeted queue with follow-ups and suppression; verdict ledger checked against repro results; final report data |
| `report_html.py` | `report.json`, `report.md` and a self-contained, theme-aware `report.html` (publishable as a claude.ai artifact) |
| `../hooks/guard.py` | PreToolUse guard: blocks live exchange hosts, credentials and destructive commands in audit runs and reproduction files |
| `../mcp_server.py` | MCP server exposing `audit_map`, `audit_trace`, `audit_trace_import`, `audit_lint_import`, `run_repro`, `audit_queue`, `audit_record`, `audit_report` over stdio |
| `../scripts/bootstrap.py` | Builds a private virtualenv and re-execs the CLI or MCP server under it if the launching `python3` lacks Argus's dependencies |
| `../scripts/ci_helpers.py` | The two small helpers `action.yml` calls (step outputs, severity gate), kept as real scripts rather than inline YAML |

Plugin components outside `scripts/`: `skills/audit`, `skills/audit-map`, `skills/audit-trace`,
`skills/audit-investigate`, `agents/investigator.md`, `hooks/hooks.json`, `.mcp.json`.

Files under `<repo>/.audit/`: `map.*` (static), `scip/` (indexes), `trace/` and
`trace.*` (runtime), `lint.*` (linter evidence), `repros/` and `repro.*`
(reproductions), `queue.json` (the current round), `verdicts.json` (the
ledger: budget, rounds, verdicts, follow-ups), and `report.*` (final).

**Scope: Rust, Python, JavaScript/TypeScript, Go, Java, C/C++.** Every
capability targets these six. [PLAN.md](PLAN.md#language-parity) keeps a
parity matrix of which language has which capability, verified or not. To add
a language or library, add or extend a `LangSpec` in `langs/` and a fixture in
`tests/fixtures/lang/`, and add a probe scaffold in `repro/native.py` with a
test in `tests/test_native.py`. To add a runtime adapter for another language,
write the `calls` and `stalls` tables from `trace/store.py` and read the
`AUDIT_TRACE_*` variables set by `trace/run.py`.

**In CI.** [`action.yml`](action.yml) is a composite GitHub Action for auditing
another repository: static `map`, optional linter SARIF, a job summary, an
uploaded `.audit/` artifact, and an optional severity gate:

```yaml
- uses: actions/checkout@v4
- uses: <org>/Argus@main
  with:
    fail-on: high        # optional: fail the step on any high-severity finding
    run-linters: true    # optional: fold in ruff / golangci-lint SARIF if installed
```

It costs no AI tokens (it is static analysis only). It has not been run on
GitHub yet.

**MCP responses are summaries by default.** `audit_map`, `audit_trace` and
`audit_trace_import` return counts plus the 10 most severe findings and the
path to the full file; pass `full=true` for everything inline. This keeps large
repos from spending thousands of tokens on findings you may not need in
context.

**Measuring quality.** `python tests/eval/run.py` scores Argus's static map
against ground-truth cases in `tests/eval/corpus.yaml` (recall by rule and
language, plus `must_be_absent` false-positive checks). It ships with 3 local
cases that keep the harness itself honest; **real-repository cases are not
collected yet**, so treat its 100% as "the harness works", not "Argus is good
on real code". See [PLAN.md](PLAN.md#getting-to-full-parity-milestone-m5).
Needs `pip install -r tests/eval/requirements.txt`.

Tests: `python -m pytest tests`. Toolchain-dependent tests (SCIP via
rust-analyzer/scip-python/scip-typescript/scip-go; native probes via cargo,
node, tsx, javac, gcc and g++; the real OpenTelemetry SDKs) skip when the
toolchain is missing. The OpenTelemetry Java agent test needs a downloaded
`opentelemetry-javaagent.jar` (`ARGUS_OTEL_JAVAAGENT`) and is not required for
non-Java repos: the Java agent method-list derivation and SCIP-java only run
when `.java` files or JVM findings are present, and `--otlp` is opt-in, so
auditing a repo in another language never touches Java-specific code.
