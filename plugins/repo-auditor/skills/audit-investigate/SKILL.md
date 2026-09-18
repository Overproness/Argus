---
name: audit-investigate
description: Verify audit findings by reproduction. Runs one investigator subagent per finding from .audit/map.json (and .audit/trace.json evidence when present); each writes and runs a test that triggers the predicted effect under controlled conditions (latency injection, dead peer, scaling inputs) and reports a verdict with numbers. Use after audit-map or audit-trace when the user wants findings proven, asks "is this real", or wants reproduction tests for the risks found.
argument-hint: "[repo path] [--max N] [--rule blocking-in-async,...]"
---

# Audit investigate

A finding becomes real when a test that triggers it runs. This skill fans out
one investigator per finding and collects the verdicts.

## 1. Select findings

Read `.audit/map.json` (run `audit-map` if missing). If `.audit/trace.json`
exists, read it too. Select, in this order, up to `--max` (default 5):

1. `high` and `medium` findings whose trace evidence is `confirmed` or absent;
2. `needs-evidence` findings from the audit-map triage;
3. anything the user named with `--rule` or by function.

Skip findings the trace marked `not-observed` unless the user asks; say so.
Skip `info` findings unless the user asks.

Python only for now. For other languages, tell the user which findings would
need a harness that does not exist yet.

## 2. Run investigators

For each selected finding, launch the `Argus:investigator` agent with a
self-contained prompt: repo path, plugin root (`${CLAUDE_PLUGIN_ROOT}`), and
the finding as JSON (rule, severity, function, file, line, message, chain,
trace evidence). Run at most 3 in parallel. Do not investigate the same
function twice in one round; merge findings that share a leaf.

Each investigator returns one JSON verdict. Collect them.

## 3. Aggregate

```bash
python "<plugin-root>/scripts/auditor_cli.py" repro "<repo>"
```

This reruns every reproduction under `.audit/repros/` and writes
`.audit/repro.md` and `.audit/repro.json` with per-test outcomes and the
evidence lines. A verdict only stands if its test still passes here.

## 4. Report

A table: finding · verdict · trigger · measured effect · extreme case ·
smallest fix · repro file. Then:

- **Rejected** findings with the investigator's reason (these improve the rules).
- **Inconclusive** ones with what blocked them (missing mock, non-Python).
- The reproduction files stay under `.audit/repros/` so the user can move
  the useful ones into the test suite.

Do not fix code in this skill. The user decides which fixes to apply, and the
`audit-map` fix guardrails apply when they do.
