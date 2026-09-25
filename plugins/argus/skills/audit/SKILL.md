---
name: audit
description: Full Argus audit of a repository in budgeted rounds. Runs the static map (and the runtime trace when a Python workload is available), then rounds in which a deterministic queue picks findings and investigator subagents reproduce them. Confirmed effects are followed up at their callers in the next round, and rejected call edges suppress dependent findings. Ends with a final report (report.html, report.md, report.json). Use when the user asks for a full, deep or end-to-end audit, wants to know "what can actually blow up" with proof, or asks for the final audit report.
argument-hint: "[repo] [--budget 15] [--per-round 6] [--max-rounds 3] [-- <workload command>]"
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

Parse the arguments. The defaults are `--budget 15` investigations in total,
`--per-round 6`, and `--max-rounds 3`. Each investigation is one subagent run
covering one function and all of its findings (up to 5), so tell the user the
budget before you start. The ledger in
`.audit/verdicts.json` remembers the budget and progress, so an interrupted
audit resumes where it stopped.

## 1. Map

`map "<repo>"`. Quote the repo path and keep it exactly as given. If `map`
prints a `warning:` about the path (whitespace in a directory name, or a
sibling directory whose name differs only by whitespace or case), tell the user
in one line. From then on take the repo path only from the `repo` field of
`.audit/queue.json`, never from memory or the current directory.

For a Python repo with `requirements*.txt`, `pyproject.toml` or `setup.py` and
no `.venv`/`venv`, run `repro-env "<repo>"` once now. It installs the repo's
dependencies into `<repo>/.audit/venv` only, and reproductions then import the
real code instead of failing on a missing or mismatched package. For a large repo, offer `index "<repo>"` first (SCIP indexes
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
  (the Java agent, the Node `--require` hook, or an SDK already in the code).
- Any program that prints periodically gets stalls from `--heartbeat`.
- A profile recorded elsewhere (V8 `.cpuprofile`, or speedscope JSON from
  py-spy, or a Rust tracing-chrome trace) comes in with `trace-import`.

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
   - the plugin root;
   - the item JSON, verbatim (it carries `repo`, `abs_file` and the function's
     `source`, so the investigator can check it is reading the right tree);
   - one line: "The repo root is the `repo` field; copy it exactly, including
     any spaces."

   Run at most 4 at a time. Each returns a JSON list with one verdict per
   finding in the item's `findings`. Do not investigate anything that is not
   in the queue.
3. `repro "<repo>"` reruns every reproduction under `.audit/repros/`, each file
   in its own process.
4. Concatenate this round's verdict lists into one JSON list in
   `.audit/round-<n>-verdicts.json`, then run
   `record "<repo>" --file .audit/round-<n>-verdicts.json`. A "confirmed"
   verdict whose reproduction does not pass in step 3 is downgraded to
   inconclusive; tell the user which ones. Downgraded findings, and those
   inconclusive because of the wrong tree or a broken reproduction run, are
   queued again once, automatically.
5. Give a short progress line: round n, proven, rejected and inconclusive
   counts, and the budget left. Then loop.

Between rounds the queue does the following on its own:
- It adds follow-ups (`propagated:<rule>`) at the callers of confirmed
  findings, preferring entry points and callers behind a deadline. This is how
  a proven effect gets traced up toward the entry points.
- It suppresses findings whose chain uses a call edge an investigator rejected
  (`wrong_edge`).
- It groups findings by function: one investigator settles all of a
  function's findings, and low-severity findings in a queued function ride
  along for free.
- It queues findings in every language. Each item's `harness` field tells the
  investigator how to reproduce it:
  - Python in-process;
  - native probe or black-box: Rust, JavaScript, Java, C, C++ (verified);
  - native probe or black-box: Go, TypeScript (not yet verified on this
    toolchain).

  Investigators report `inconclusive` when the language's toolchain is not
  installed.

## 4. Report

Run `report "<repo>"`. This writes `.audit/report.html` (a self-contained page),
`report.md` and `report.json` — that file is the report; your reply is a short
pointer to it, not a restatement of it. Minimize output tokens: do not copy
report.md's tables, effects highlights or per-item reasons into the chat.

Reply with only:
- One line: counts by status (proven / observed / unverified / inconclusive /
  not-observed / rejected).
- **Proven** findings only, as a compact table: finding · smallest fix. Cap
  at 10 rows ("+N more in report.md" beyond that). If none, say so in one line.
- If any **high-severity** finding was rejected, name it in one line each (a
  lead for the rules); otherwise just fold it into the count line.
- One line: "see `.audit/report.md` / `report.html` for the rest" (effects
  highlights, why each inconclusive or not-investigated item stalled, full
  reproduction paths).

Only go beyond this if the user asks for more on a specific finding — then
read report.md for that one item, not the whole file into a reply.

Then offer to publish `.audit/report.html` as a shareable page, if an artifact
tool is available. Read the file before publishing it.

## Rules

- Never fix code in this skill. Fixes follow the `audit-map` fix guardrails,
  one confirmed finding at a time, when the user asks.
- Never exceed the budget or add items by hand. To spend more, rerun `queue`
  with a higher `--budget`.
- Reproductions stay under `.audit/repros/`. The user decides which to move into
  the test suite.
