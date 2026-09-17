# OurAgenticWorkflow

Claude Code plugin marketplace for agentic code-audit workflows. See [PLAN.md](PLAN.md).

## repo-auditor

A static audit map for 12 languages (Rust, Python, JavaScript/TypeScript, Go,
Java, Kotlin, Scala, C#, Swift, C, C++, Ruby, PHP). It produces:

- the call graph;
- a list of every I/O and external call (network, database, filesystem, sleeps,
  processes, waits), each marked blocking or not, with its context and timeout
  status;
- findings: blocking calls on async paths (followed through call chains),
  missing timeouts, I/O in loops (N+1), sync locks held across `await`,
  sync-over-async, unbounded goroutines, `defer` in loops, recursion, nested
  loops;
- hotspots.

```bash
pip install -r plugins/repo-auditor/requirements.txt

python plugins/repo-auditor/scripts/auditor_cli.py langs            # supported languages
python plugins/repo-auditor/scripts/auditor_cli.py map path/to/repo  # -> path/to/repo/.audit/map.{json,md}
python plugins/repo-auditor/scripts/auditor_cli.py index path/to/repo  # optional: SCIP indexes for precise calls

# Try the plugin without installing it
claude --plugin-dir plugins/repo-auditor
#   then: /repo-auditor:audit-map path/to/repo

# Install it through this marketplace (inside Claude Code)
/plugin marketplace add D:/GitHub/OurAgenticWorkflow
/plugin install repo-auditor@our-agentic-workflow
```

Layout of `plugins/repo-auditor/scripts/auditor/`:

| File | Role |
|---|---|
| `extract.py` | Generic tree-sitter extraction, driven by a language spec |
| `langs/*.py` | One spec per language: node types, async rules, I/O rules, hooks |
| `analysis.py` | Call resolution, I/O classification, passing effects between functions, findings |
| `scip.py` | Precise call resolution from SCIP indexes (no protobuf dependency), plus the indexer runner |
| `report.py` | `map.json` and `map.md` output |

To add a language or library, add or extend a `LangSpec` in `langs/` and a
fixture in `tests/fixtures/lang/`.

Tests: `python -m pytest tests`. The SCIP test runs when `rust-analyzer` is installed.
