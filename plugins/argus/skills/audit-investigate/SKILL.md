---
name: audit-investigate
description: Verify audit findings by reproduction, one round. The deterministic queue ranks findings from .audit/map.json (and .audit/trace.json evidence when present); one investigator subagent per queued finding writes and runs a test that triggers the predicted effect under controlled conditions (latency injection, dead peer, simulated outage, scaling inputs) and reports a verdict with numbers, which is recorded in the verdict ledger. Use after audit-map or audit-trace when the user wants findings proven, asks "is this real", or wants reproduction tests. For several rounds and a final report, use the `audit` skill.
argument-hint: "[repo path] [--max N] [--rule blocking-in-async,...]"
---

# Audit investigate (one round)

A finding becomes real when a test that triggers it runs. This skill runs one
round: the queue picks, investigators reproduce, and the ledger records.

The CLI is `python "<plugin-root>/scripts/auditor_cli.py"`. The plugin root is
two directories above this skill's base directory, or `${CLAUDE_PLUGIN_ROOT}`.

## 1. Queue

Run `map` first if `.audit/map.json` is missing. Then:

```bash
python "<plugin-root>/scripts/auditor_cli.py" queue "<repo>" --per-round N [--rule R ...]
```

`N` is `--max` (default 5). Pass `--rule` for each rule the user named. The
queue:
- ranks findings by severity, confidence, trace evidence and hotspot score;
- merges findings that share a leaf;
- skips findings with verdicts, `info`/`low` severity, and `not-observed`
  evidence;
- skips languages without a harness (only Python has one).

Read `.audit/queue.json`. Tell the user what was skipped and why, especially
non-Python findings, which need a harness that does not exist yet.

## 2. Run investigators

For each item, launch the `Argus:investigator` agent with a self-contained
prompt containing:
- the repo path;
- the plugin root;
- the item JSON, verbatim.

Run at most 3 at a time. Each returns one JSON verdict.

## 3. Aggregate and record

```bash
python "<plugin-root>/scripts/auditor_cli.py" repro "<repo>"
python "<plugin-root>/scripts/auditor_cli.py" record "<repo>" --file .audit/round-verdicts.json
```

`repro` reruns every reproduction under `.audit/repros/`. Write the collected
verdicts as a JSON list to `.audit/round-verdicts.json` before running
`record`. A verdict only stands if its test passes in the aggregate run; the
ledger downgrades any that do not.

## 4. Report

A table: finding · verdict · trigger · measured effect · extreme case ·
smallest fix · repro file. Then:

- **Rejected** findings with the investigator's reason. These improve the rules.
- **Inconclusive** findings with what blocked them (a missing mock, a
  non-Python target).
- The reproduction files stay under `.audit/repros/`, so the user can move the
  useful ones into the test suite.
- `report "<repo>"` writes the combined report (`.audit/report.html`) whenever
  the user wants it.

Do not fix code in this skill. The user decides which fixes to apply, and the
`audit-map` fix guardrails apply when they do.
