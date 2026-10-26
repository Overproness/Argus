# Argus

**Find runtime risks. Investigate them. Keep the evidence.**

Argus is a Claude Code plugin for auditing concurrency, I/O, and performance
risks. It maps a codebase, ranks suspicious functions, and guides an investigator
to write and run reproductions against the real application. Reports distinguish
executed proof from runtime observations, unresolved leads, and rejected claims.

Built by the CODEFORGE AI Team. Available through this repository's own Claude
Code marketplace, with a CLI and an MCP server for other agent clients.

The free [Argus Audit setup skill](skills/argus-audit/SKILL.md) explains how to
connect those tools in another agent. It requires the full Argus installation.

## Install and run

Use **Python 3.12+** for the full workflow, including the built-in Python tracer.
The plugin starts its MCP server with `python3`, so make that command available
in the environment where Claude Code runs. Native reproductions also need the
target language's compiler or runtime and the application's dependencies.

Inside Claude Code:

```text
/plugin marketplace add Overproness/Argus
/plugin install argus@argus
```

Start a new Claude Code session, then audit a local checkout:

```text
/argus:audit /path/to/repo --budget 15 --per-round 6 --max-rounds 3
```

On first use, Argus installs missing Python dependencies into a private
environment under `~/.cache/argus/venv`; this requires package-download access.
Set `ARGUS_HOME` to change its cache directory. To manage dependencies yourself,
install `plugins/argus/requirements.txt` in your chosen Python environment.

The default budget is **15 function investigations**, up to **6 per round**,
across **3 rounds**. Nearby functions can share an investigator's setup while
retaining separate findings and verdicts. A budget limits investigations; it
does not guarantee that every lead will be examined. Low-severity leads use
remaining capacity after higher-severity items.

For development, clone the repository and load your checkout directly:

```sh
claude --plugin-dir ./plugins/argus
```

## What it looks for

Argus analyzes **Python, Rust, JavaScript, TypeScript, Go, Java, C, and C++**.
Its language-specific rules and runtime integrations differ; use `langs` to see
the exact supported extensions.

| Risk | Examples of leads |
|---|---|
| Blocked concurrency | Blocking I/O or CPU work on async paths; synchronous locks held across `await`; sync-over-async |
| Requests that never finish | Missing I/O deadlines; retries without a bound; a deadline wrapping code that blocks its own executor |
| Amplified work | I/O in loops, layered retries, repeated client construction, independent sequential awaits |
| Uncontrolled lifetime or growth | Detached tasks, unbounded queues, background-task admission, cache and buffer growth |
| Incorrect assumptions | Wall-clock TTL arithmetic, floating-point money, error handling and other source-pattern risks |

The map includes functions, call edges, known external calls, timeout information,
and ranked leads. Optional SCIP indexes improve call resolution, while SARIF
imports place linter results alongside the relevant functions.

Current coverage includes non-literal deadline values, Rust receiver-based
spawns such as `handle.spawn(...)`, multiline await bindings, and block-scoped
lock guards. These rules remain heuristics that need investigation in context.

## From lead to finding

1. **Map:** parse the source and identify risky paths.
2. **Observe:** optionally exercise the application or import a recorded trace.
3. **Investigate:** select functions within the budget and run reproductions
   under `.audit/repros/`, using the actual application source.
4. **Record:** retain the result, measurements, and proof associated with each
   finding. Setup failures and unfinished investigations stay inconclusive.
5. **Report:** write `.audit/report.html`, `.audit/report.md`, and
   `.audit/report.json`.

The report separates **proven**, **observed**, **unverified**, **inconclusive**,
**not-observed**, and **rejected** results. A static warning alone is a lead.
Fault-injected observations describe behavior under those injected conditions.
Absence of a stall under one workload does not prove that a path is safe.

Python reproductions can run in-process. Other languages can use a small native
probe that depends on the application by path, or a black-box workload against
a local fault server. The server can inject latency, hangs, connection resets,
and failures while counting connections.

## CLI and MCP

The deterministic CLI can map and rank code without a model. Writing and
interpreting new reproductions is the agent's part of the workflow.

From a repository checkout:

```sh
python plugins/argus/scripts/auditor_cli.py doctor
python plugins/argus/scripts/auditor_cli.py langs
python plugins/argus/scripts/auditor_cli.py map /path/to/repo
python plugins/argus/scripts/auditor_cli.py queue /path/to/repo --budget 15 --per-round 6 --max-rounds 3
```

Useful commands for collecting and reviewing evidence:

```sh
# Observe an existing workload; use the application's interpreter/environment.
python plugins/argus/scripts/auditor_cli.py trace /path/to/repo -- python -m pytest tests
python plugins/argus/scripts/auditor_cli.py trace /path/to/repo -- npm test

# Import evidence recorded elsewhere.
python plugins/argus/scripts/auditor_cli.py trace-import /path/to/repo --chrome trace.json
python plugins/argus/scripts/auditor_cli.py trace-import /path/to/repo --profile profile.cpuprofile
python plugins/argus/scripts/auditor_cli.py lint-import /path/to/repo --sarif results.sarif

# Run agent-written reproductions and record their verdicts.
python plugins/argus/scripts/auditor_cli.py repro-env /path/to/repo
python plugins/argus/scripts/auditor_cli.py repro /path/to/repo
python plugins/argus/scripts/auditor_cli.py record /path/to/repo --file verdicts.json
python plugins/argus/scripts/auditor_cli.py report /path/to/repo
```

`repro-env` is for Python applications: it installs their dependencies under
`.audit/venv`. It is separate from Argus's own dependency environment.

Runtime evidence comes from Python's built-in function tracer on Python 3.12+,
Node's profiler and V8 coverage, imported Rust `tracing-chrome` traces,
OpenTelemetry spans, heartbeat gaps, or imported V8/speedscope profiles.
OpenTelemetry collection is opt-in with `trace --otlp`; external programs must
already be instrumented. See the [trace guide](plugins/argus/skills/audit-trace/SKILL.md)
for setup and attribution limits.

Other MCP clients can start the same tools over stdio:

```sh
python plugins/argus/scripts/mcp_server.py
```

Tools include `audit_status`, `audit_map`, `audit_trace`, `audit_trace_import`,
`audit_lint_import`, `audit_repro_env`, `run_repro`, `audit_queue`, `audit_record`,
and `audit_report`. Map and trace responses return summaries by default, with
paths to full output files; `full=true` requests the complete result inline.

## Updates and runtime identity

This Git-hosted marketplace uses commit-based versions. Keep `version` absent
from the plugin and marketplace manifests; a fixed version string can leave
users on a stale cached payload when later commits reuse it.

Update from a terminal, then start a new Claude Code session:

```sh
claude plugin marketplace update argus
claude plugin update argus@argus
```

Before comparing audit runs, run CLI `doctor` in the intended checkout and call
MCP `audit_status` in each audit session. Record the plugin path and payload hash,
and compare them with the intended installation. Restart if `restart_required`
is true. A fresh CLI process does not identify an already-running MCP server.

See [PUBLISHING.md](PUBLISHING.md) for the maintainer release workflow.

## GitHub Actions

The [composite action](action.yml) creates a static map, writes a job summary,
and optionally imports SARIF from installed linters. It needs no model API key.
It does not run the agent investigation workflow.

```yaml
- uses: actions/checkout@v4
- uses: Overproness/Argus@main # Pin a reviewed commit SHA for a stable workflow.
  with:
    path: .
    run-linters: false
    # fail-on: high          # Optional gate on static leads, not proven bugs.
```

Review audit artifacts before sharing them: they can contain source excerpts,
local paths, traces, and application data.

## Scope and evaluation

Argus focuses on runtime and composition risks recognizable from source and
executable behavior. It is not a general domain-logic oracle. Dynamic dispatch,
generated code, custom libraries, deployment policies, and incomplete workloads
can leave gaps. Rules can produce false positives, and a finite budget can leave
real issues uninvestigated. A report is not a certification that a repository
is bug-free.

The supplied regression fixtures and evaluation harness exercise known cases.
Cases used to improve detectors are tuning data. Static detection, queue
coverage, and model-generated proof are separate measurements. Development
results do not establish recall on unseen repositories or an advantage over
a plain model audit. Local benchmark targets, answer keys, and raw audit
conversations are excluded from the current release tree. Materials committed
previously remain accessible in Git history; those answers are development data.

Run workloads and reproductions against mocks or sandboxes in a disposable
environment. The plugin's guardrails are not an operating-system sandbox;
never provide production credentials, live exchanges, or shared databases.

## Development

```sh
python -m pip install -r plugins/argus/requirements.txt -r tests/fixtures/main_py_comparison/requirements.txt
python -m pytest tests -q
```

Tests requiring compilers, runtimes, SCIP indexers, or OpenTelemetry agents skip
when those dependencies are unavailable. CI runs the core suite on Linux,
macOS, and Windows with Python 3.12. The evaluation harness has separate
dependencies in `tests/eval/requirements.txt`.

Start with [AGENTS.md](AGENTS.md), the [audit workflow](plugins/argus/skills/audit/SKILL.md),
and the [investigation guide](plugins/argus/skills/audit-investigate/SKILL.md).
Implementation lives in `plugins/argus/scripts/auditor/`; language specs and
rules are under `langs/`, runtime adapters under `trace/`, and reproduction
support under `repro/`.
