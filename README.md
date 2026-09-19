# Argus

Claude Code plugin marketplace for agentic code-audit workflows, by the CODEFORGE AI Team. See [PLAN.md](PLAN.md).

## Argus (plugin)

A static audit map for 12 languages (Rust, Python, JavaScript/TypeScript, Go,
Java, Kotlin, Scala, C#, Swift, C, C++, Ruby, PHP), plus a runtime tracer
(Python for now) that turns the map's findings into evidence. It produces:

- the call graph;
- a list of every I/O and external call (network, database, filesystem, sleeps,
  processes, waits), each marked blocking or not, with its context and timeout
  status;
- findings: blocking calls on async paths (followed through call chains),
  missing timeouts, I/O in loops (N+1), sync locks held across `await`,
  sync-over-async, unbounded goroutines, `defer` in loops, recursion, nested
  loops;
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
  - `unwrap`/`expect`/`try!` on network results;
  - Go I/O errors discarded with `_`;
- scaling projections: super-linear functions projected to the largest input
  seen upstream, or to a size given with `--assume`;
- reproductions: an investigator subagent writes a test per finding that
  triggers the predicted effect under controlled conditions (injected latency,
  dead peer, simulated outage, scaling inputs), runs it, and reports a verdict
  with numbers;
- budgeted rounds (M4): a deterministic queue picks what to reproduce next,
  follows confirmed effects up to their callers, and suppresses findings whose
  call edge an investigator rejected;
- a final report (`report.html`, `report.md`, `report.json`) that ranks every
  finding by how far it got: proven, observed, unverified, inconclusive,
  not observed, rejected.

```bash
pip install -r plugins/argus/requirements.txt

python plugins/argus/scripts/auditor_cli.py langs             # supported languages
python plugins/argus/scripts/auditor_cli.py map path/to/repo  # -> path/to/repo/.audit/map.{json,md}
python plugins/argus/scripts/auditor_cli.py index path/to/repo  # optional: SCIP indexes for precise calls
python plugins/argus/scripts/auditor_cli.py trace path/to/repo -- python -m pytest tests
#   -> path/to/repo/.audit/trace.{json,md}; traced program needs Python 3.12+
python plugins/argus/scripts/auditor_cli.py trace-report path/to/repo --assume rows=50000  # rebuild, project sizes
python plugins/argus/scripts/auditor_cli.py repro path/to/repo   # run .audit/repros/test_*.py -> .audit/repro.{json,md}
python plugins/argus/scripts/auditor_cli.py queue path/to/repo --budget 10 --per-round 5 --max-rounds 3
python plugins/argus/scripts/auditor_cli.py record path/to/repo --file verdicts.json   # or --file - for stdin
python plugins/argus/scripts/auditor_cli.py report path/to/repo  # -> .audit/report.{json,md,html}

# Try the plugin without installing it
claude --plugin-dir plugins/argus
#   /Argus:audit path/to/repo --budget 10 [-- <workload>]   everything, in budgeted rounds, ending in the report
#   /Argus:audit-map path/to/repo                       static map + triage
#   /Argus:audit-trace path/to/repo -- <command>        runtime evidence
#   /Argus:audit-investigate path/to/repo               one round of reproductions via the Argus:investigator agent
#   MCP tools (any client, see plugins/argus/.mcp.json):
#     audit_map, audit_trace, run_repro, audit_queue, audit_record, audit_report

# Install it through this marketplace (inside Claude Code)
/plugin marketplace add <path or git URL of this repo>
/plugin install Argus@Argus
```

Layout of `plugins/argus/scripts/auditor/`:

| File | Role |
|---|---|
| `extract.py` | Generic tree-sitter extraction, driven by a language spec |
| `langs/*.py` | One spec per language: node types, async rules, I/O rules, hooks |
| `analysis.py` | Call resolution, I/O classification, passing effects between functions, findings |
| `effects.py` | Worst-case waits, deadlines, retry multipliers and crash-on-error, propagated across the call graph |
| `timeouts.py` | Durations out of source text: timeout arguments, deadline wrappers, sleeps, library defaults |
| `scip.py` | Precise call resolution from SCIP indexes (no protobuf dependency), plus the indexer runner |
| `report.py` | `map.json` and `map.md` output |
| `trace/store.py` | SQLite trace store: one file per traced process, merged on read |
| `trace/py/tracer.py` | Python tracer (`sys.monitoring`): per-call timing in slices, self time, event-loop stalls, argument sizes |
| `trace/run.py` | Runs a command with tracers attached through `AUDIT_TRACE_*` environment variables |
| `trace/fit.py` | Complexity fitting: (input size, duration) samples to O(n^k) |
| `trace/evidence.py` | Joins traces to `map.json`; writes `trace.json` and `trace.md` |
| `repro/harness.py` | Reproduction helpers: `latency`, `hang`, `fail_connect`, `count_connects`, `refuse_remote`, `loop_monitor`, `call_with_deadline`, `scaling`, `count_calls`; each emits `@@evidence` lines |
| `repro/runner.py` | Runs `.audit/repros/test_*.py` under pytest, collects outcomes and evidence into `repro.{json,md}` |
| `orchestrate.py` | The round loop: ranked, budgeted queue with follow-ups and suppression; verdict ledger checked against repro results; final report data |
| `report_html.py` | `report.json`, `report.md` and a self-contained, theme-aware `report.html` (publishable as a claude.ai artifact) |
| `../hooks/guard.py` | PreToolUse guard: blocks live exchange hosts, credentials and destructive commands in audit runs and reproduction files |
| `../mcp_server.py` | MCP server exposing `audit_map`, `audit_trace`, `run_repro`, `audit_queue`, `audit_record`, `audit_report` over stdio |

Plugin components outside `scripts/`: `skills/audit`, `skills/audit-map`, `skills/audit-trace`,
`skills/audit-investigate`, `agents/investigator.md`, `hooks/hooks.json`, `.mcp.json`.

Files under `<repo>/.audit/`: `map.*` (static), `scip/` (indexes), `trace/` and
`trace.*` (runtime), `repros/` and `repro.*` (reproductions), `queue.json` (the
current round), `verdicts.json` (the ledger: budget, rounds, verdicts,
follow-ups), and `report.*` (final).

To add a language or library, add or extend a `LangSpec` in `langs/` and a
fixture in `tests/fixtures/lang/`. To add a runtime adapter for another
language, write the `calls` and `stalls` tables from `trace/store.py` and read
the `AUDIT_TRACE_*` variables set by `trace/run.py`.

Tests: `python -m pytest tests`. The SCIP test runs when `rust-analyzer` is installed.
