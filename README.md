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
  static rules did not predict.

```bash
pip install -r plugins/repo-auditor/requirements.txt

python plugins/repo-auditor/scripts/auditor_cli.py langs             # supported languages
python plugins/repo-auditor/scripts/auditor_cli.py map path/to/repo  # -> path/to/repo/.audit/map.{json,md}
python plugins/repo-auditor/scripts/auditor_cli.py index path/to/repo  # optional: SCIP indexes for precise calls
python plugins/repo-auditor/scripts/auditor_cli.py trace path/to/repo -- python -m pytest tests
#   -> path/to/repo/.audit/trace.{json,md}; traced program needs Python 3.12+
python plugins/repo-auditor/scripts/auditor_cli.py trace-report path/to/repo  # rebuild from .audit/trace/*.db

# Try the plugin without installing it
claude --plugin-dir plugins/repo-auditor
#   then: /Argus:audit-map path/to/repo  and  /Argus:audit-trace path/to/repo -- <command>

# Install it through this marketplace (inside Claude Code)
/plugin marketplace add <path or git URL of this repo>
/plugin install Argus@Argus
```

Layout of `plugins/repo-auditor/scripts/auditor/`:

| File | Role |
|---|---|
| `extract.py` | Generic tree-sitter extraction, driven by a language spec |
| `langs/*.py` | One spec per language: node types, async rules, I/O rules, hooks |
| `analysis.py` | Call resolution, I/O classification, passing effects between functions, findings |
| `scip.py` | Precise call resolution from SCIP indexes (no protobuf dependency), plus the indexer runner |
| `report.py` | `map.json` and `map.md` output |
| `trace/store.py` | SQLite trace store: one file per traced process, merged on read |
| `trace/py/tracer.py` | Python tracer (`sys.monitoring`): per-call timing in slices, self time, event-loop stalls, argument sizes |
| `trace/run.py` | Runs a command with tracers attached through `AUDIT_TRACE_*` environment variables |
| `trace/fit.py` | Complexity fitting: (input size, duration) samples to O(n^k) |
| `trace/evidence.py` | Joins traces to `map.json`; writes `trace.json` and `trace.md` |

To add a language or library, add or extend a `LangSpec` in `langs/` and a
fixture in `tests/fixtures/lang/`. To add a runtime adapter for another
language, write the `calls` and `stalls` tables from `trace/store.py` and read
the `AUDIT_TRACE_*` variables set by `trace/run.py`.

Tests: `python -m pytest tests`. The SCIP test runs when `rust-analyzer` is installed.
