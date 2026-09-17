---
name: audit-map
description: Build a static audit map of a repository in any major language (Rust, Python, JS/TS, Go, Java, Kotlin, Scala, C#, Swift, C/C++, Ruby, PHP) and triage its findings. The map covers the call graph, a list of every I/O and external call, and hotspots. Findings include blocking calls on async paths (including through call chains), external calls without timeouts, I/O in loops (N+1), sync locks held across await, sync-over-async, unbounded goroutines, and recursion. Use when the user wants to audit a repo for performance or reliability risks, find extreme cases, or asks "what could blow up in this codebase".
argument-hint: "[repo path, default: current directory]"
---

# Audit map

The script does the analysis. You triage what it produces. Do not freelance:
every problem you report must come from `map.json` or from code you read to
confirm or reject one of its findings.

## 1. Run the mapper

The CLI is at `scripts/auditor_cli.py` in this plugin's root, which is two
directories above this skill's base directory. `${CLAUDE_PLUGIN_ROOT}` also
points to the plugin root.

```bash
python "<plugin-root>/scripts/auditor_cli.py" map "<repo>"
```

- `<repo>` is the argument the user gave (`$ARGUMENTS`), or the current working directory.
- Exit code 2 means missing dependencies. Run
  `pip install -r "<plugin-root>/requirements.txt"` and try again.
- Test code is skipped by default. Add `--include-tests` only if the user asks.
- Output goes to `<repo>/.audit/map.json` and `map.md`. Suggest adding `.audit/`
  to `.gitignore` if it isn't there.

**Precise call resolution (optional, recommended for large repos).** Name
matching can link a call to the wrong function. Ask the user before running
`python "<plugin-root>/scripts/auditor_cli.py" index "<repo>"`, because indexers
may compile the project and take minutes. It runs the SCIP indexer for each
language it detects and prints install hints for missing ones. The next `map`
run picks the indexes up automatically.

## 2. Triage

Read `.audit/map.md`. Then, for each **high** and **medium** finding, in order:

1. Open the code at the reported location and at each step of `chain`.
2. Confirm or reject it. Confidence levels:
   - `exact` / `scip`: resolved by scope, path or index. Usually right.
   - `unique`: the only function with that name and a matching argument count.
     Check that the receiver really has that type.
   - `name`: several candidates. Often wrong; the severity is already reduced.
   - `heuristic`: the I/O classification comes from a library pattern. Check the
     import and the receiver's type.

   Typical false positives:
   - a same-named method on an external type;
   - a lock that is actually async (`tokio::sync`, `asyncio.Lock`);
   - a timeout configured on a client built in another file (the finding names
     candidate files);
   - code that only runs at startup or in a CLI, where blocking is harmless.
3. Label it **confirmed**, **rejected (reason)** or **needs-evidence**.
   Blocking-in-async and timeout findings are usually confirmable by reading the
   code. Magnitude ("how slow", "how often", "how big does n get") needs runtime
   evidence (M2 tracing or fault injection). Say so instead of guessing.
4. Library default timeouts quoted in findings come from the tool's rule
   table. If you doubt one, check the library source or docs for the version in
   the lockfile before contradicting it.

Low and info findings: summarize them in one line each unless the user asks for more.

## 3. Report

Reply with:

- A short table of confirmed findings: severity, location, the chain in one
  line, why it matters under an extreme case (for example, "exchange takes 30s
  → runtime worker stalls → other tasks miss their ticks"), and the smallest fix.
  Typical fixes: offload to a blocking pool, use the async client, add a timeout
  or deadline, drop the guard before `await`, batch or bound concurrency.
- Rejected findings, one line each with the reason. This is how the rules get
  improved.
- Findings that need evidence, and the experiment that would settle each one.
- The top 5 hotspots from the map, as candidates for deeper investigation.

Do not add problems that are not in the map. If you notice something outside it
while reading code, list it separately under "Unmapped observations
(unverified)".
