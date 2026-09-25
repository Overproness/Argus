# Argus for agents that are not Claude Code

Argus audits a codebase: a fixed tool lists risky points, you decide what to investigate, and every
finding needs evidence (a trace or a reproduction that ran). Do not report a suspicion as a finding.

## Setup
```
pip install -r plugins/argus/requirements.txt
```
Everything runs through one CLI: `python plugins/argus/scripts/auditor_cli.py <command> <repo>`.
The same operations are MCP tools (`python plugins/argus/scripts/mcp_server.py`, stdio): `audit_map`,
`audit_trace`, `audit_trace_import`, `audit_lint_import`, `run_repro`, `audit_repro_env`, `audit_queue`,
`audit_record`, `audit_report`.

## Workflow
1. `map <repo>` writes `.audit/map.{json,md}`: functions, call graph, external calls, findings.
2. Get evidence: `trace <repo> -- <command that exercises the code>`, or import what another tool recorded
   (`trace-import --otlp-file | --profile | --chrome | --coverage`, `lint-import --sarif`).
3. For Python, `repro-env <repo>` once so reproductions import the real code (installs into `.audit/venv`).
   Then `queue`: each item is one function with all its findings, plus `repo`, `abs_file` and the function's
   `source`. Use `repo` verbatim (it may contain spaces) and check `source` matches before judging anything.
   Investigate each item by writing a reproduction under `.audit/repros/`
   (see `plugins/argus/skills/audit-investigate/SKILL.md` and `agents/investigator.md`), run `repro`, and
   `record` the verdict. Confirmed means the reproduction failed the way the finding predicted.
4. `report <repo>` writes `.audit/report.html`.

The step-by-step instructions are in `plugins/argus/skills/*/SKILL.md` (open Agent Skills format); read them
directly. Never point a run at production credentials, live exchanges or shared databases; use a mock or sandbox.
