---
name: audit
description: Full Argus audit of a repository in budgeted rounds. Runs the static map (and the runtime trace when a Python workload is available), then rounds in which a deterministic queue picks findings and investigator subagents reproduce them. Confirmed effects are followed up at their callers in the next round, and rejected call edges suppress dependent findings. Ends with a final report (report.html, report.md, report.json). Use when the user asks for a full, deep or end-to-end audit, wants to know "what can actually blow up" with proof, or asks for the final audit report.
argument-hint: "[repo] [--budget 10] [--per-round 5] [--max-rounds 3] [-- <workload command>]"
---

# Audit (full, in rounds)

Static rules find leads, the trace adds evidence, and reproductions prove them.
This skill runs all three and spends a fixed budget of investigations on
whatever matters most. The CLI decides what to investigate and keeps the books.
You run the steps, launch the investigators and report. You do not fix code
here.

The CLI is `python "<plugin-root>/scripts/auditor_cli.py"`. The plugin root is
two directories above this skill's base directory, or `${CLAUDE_PLUGIN_ROOT}`.

## 0. Budget

Parse the arguments. The defaults are `--budget 10` investigations in total,
`--per-round 5`, and `--max-rounds 3`. Each investigation is one subagent run,
so tell the user the budget before you start. The ledger in
`.audit/verdicts.json` remembers the budget and progress, so an interrupted
audit resumes where it stopped.

## 1. Map

`map "<repo>"`. For a large repo, offer `index "<repo>"` first (SCIP indexes
for precise call resolution). Ask before running it, because indexers can take
minutes.

## 2. Trace (optional)

This step works in any language. If the user gave a workload after `--`, or the
repo has an obvious test suite and the user agrees, run
`trace "<repo>" [--otlp] [--heartbeat REGEX] -- <command>`:
- Python 3.12+ is traced automatically.
- Node (JavaScript, TypeScript) is profiled automatically: stalls with stacks
  and exact call counts.
- Other languages send OpenTelemetry spans with `--otlp` when instrumented
  (the Java agent, .NET automatic instrumentation, the Node `--require`
  hook, or an SDK already in the code).
- Any program that prints periodically gets stalls from `--heartbeat`.
- A profile recorded elsewhere (V8 `.cpuprofile`, or speedscope JSON from
  py-spy, rbspy or dotnet-trace) comes in with `trace-import --profile`.

The `audit-trace` skill has the per-language setup. Do not add instrumentation
to the repo without consent.

If the user named production sizes, pass `--assume ARG=N` so super-linear
functions are projected to those sizes. Never point a workload at production
credentials or live exchanges. The safety guard blocks the obvious cases, but
ask the user whenever you are unsure.

## 3. Rounds

Repeat:

1. `queue "<repo>" --budget B --per-round K --max-rounds R`. Pass the flags on
   the first round only; later rounds reuse the stored budget. If the output
   starts with `stop:`, go to step 4.
2. Read `.audit/queue.json`. For each entry in `items`, launch the
   `argus:investigator` agent with a self-contained prompt containing:
   - the repo path;
   - the plugin root;
   - the item JSON, verbatim.

   Run at most 3 at a time. Each returns one JSON verdict. Do not investigate
   anything that is not in the queue.
3. `repro "<repo>"` reruns every reproduction under `.audit/repros/`.
4. Write this round's verdicts as a JSON list to `.audit/round-<n>-verdicts.json`,
   then run `record "<repo>" --file .audit/round-<n>-verdicts.json`. A
   "confirmed" verdict whose reproduction does not pass in step 3 is downgraded
   to inconclusive; tell the user which ones.
5. Give a short progress line: round n, proven, rejected and inconclusive
   counts, and the budget left. Then loop.

Between rounds the queue does the following on its own:
- It adds follow-ups (`propagated:<rule>`) at the callers of confirmed
  findings, preferring entry points and callers behind a deadline. This is how
  a proven effect gets traced up toward the entry points.
- It suppresses findings whose chain uses a call edge an investigator rejected
  (`wrong_edge`).
- It queues findings in every language. Each item's `harness` field tells the
  investigator how to reproduce it:
  - Python in-process;
  - native probe or black-box: Rust, JavaScript, Java, C#, C, C++ (verified);
  - native probe templates not yet verified on a toolchain, or black-box: Go,
    TypeScript, Kotlin, Scala, Swift, Ruby, PHP.

  Investigators report `inconclusive` when the language's toolchain is not
  installed.

## 4. Report

Run `report "<repo>"`. This writes `.audit/report.html` (a self-contained page),
`report.md` and `report.json`. Read `report.md` and reply with:

- One line per status: how many findings are proven, observed, unverified,
  inconclusive, not-observed and rejected.
- A table of **proven** findings: finding · extreme case · smallest fix ·
  reproduction file.
- The effects highlights from the map:
  - entry points that can wait forever;
  - deadlines that cannot fire or are exceeded;
  - retry amplification.
- **Rejected** findings with their reasons. These improve the static rules.
- **Not investigated**, grouped by reason (budget, severity, trace
  not-observed), and **inconclusive** findings grouped by what blocked them
  (a missing toolchain, a hard-coded URL, real infrastructure), so the user
  knows what is still open.

Then offer to publish `.audit/report.html` as a shareable page, if an artifact
tool is available. Read the file before publishing it.

## Rules

- Never fix code in this skill. Fixes follow the `audit-map` fix guardrails,
  one confirmed finding at a time, when the user asks.
- Never exceed the budget or add items by hand. To spend more, rerun `queue`
  with a higher `--budget`.
- Reproductions stay under `.audit/repros/`. The user decides which to move into
  the test suite.
