---
name: argus-audit
description: Set up Argus from its public GitHub repository and run evidence-backed reliability and performance audits using its real tools. Use for Argus installation, async blocking, timeouts, retries, N+1 I/O, resource leaks, scaling risks, and reports with measured reproductions.
---

# Argus setup and code audit

This free skill explains how to get and use the full
[Argus toolset](https://github.com/Overproness/Argus). Agensi delivers these
instructions; it does not install the plugin, configure another MCP server, or
run Argus. There are no bundled programs or local skill-directory assumptions.

Requires Python 3.12+, access to the repository filesystem, file reading and
writing, and local command execution for reproductions. Native probes require
the corresponding language toolchain. The agent must have the actual Argus MCP
tools connected before performing the audit below.

## Set up Argus when needed

If Argus is absent, give the appropriate setup steps. Perform installation or
host configuration changes only when the user has authorized that setup; an
audit request alone does not authorize silently changing agent configuration.

**Claude Code:** enter these commands inside Claude Code:

```text
/plugin marketplace add Overproness/Argus
/plugin install argus@argus
```

Restart Claude Code so its Argus MCP server and plugin tools reload. The full
plugin includes audit skills, investigators, tracing, and reproduction tools.

**Other agents with local stdio MCP support:** use an existing Argus checkout
or clone the public repository into a user-chosen location:

```text
git clone https://github.com/Overproness/Argus.git "USER_CHOSEN_ARGUS_CHECKOUT"
```

In that agent's MCP settings, add a local stdio server using the user's Python
3.12+ executable as `command` and the absolute path to
`plugins/argus/scripts/mcp_server.py` inside that checkout as its single `args`
entry. Keep paths with spaces as one argument. Do not guess client-specific
configuration files or write settings without authorization. The first server
start can install dependencies into Argus's private environment and needs
package-index access. See the repository's
[current setup instructions](https://github.com/Overproness/Argus#readme).

Restart or reconnect the agent and verify its Argus tools appear. Agensi's
`get_skill` is not the Argus server. If MCP, shared filesystem access, or required
tools remain unavailable, explain the missing prerequisite and stop dependent
work. Do not substitute a plain model audit for an Argus run.

## Check the running tools

A full audit needs `audit_status`, `audit_map`, `audit_queue`, `audit_repro_env`,
`run_repro`, `audit_record`, and `audit_report`, allowing for host namespace
prefixes. Tracing additionally needs `audit_trace` or an import tool.

Call `audit_status({})`. Record its plugin path and payload hash. Compare with
the intended installation when specified. Stop if `restart_required` is true
or the payload differs; refresh and restart that server first. A fresh CLI
diagnostic does not identify an already running MCP server.

Confirm the server and agent can access the same target files. Preserve paths
returned by Argus and use the queue's `repo` verbatim, including spaces.

## Run the audit

1. Call `audit_map` with the repository path. Static results are leads. Read
   returned artifact paths when the response preview is insufficient. For a
   requested static-only audit, stop after triage and label results unverified.
2. State the full-audit budget: **15 function investigations, up to 6 per round,
   at most 3 rounds**, unless the user requests different limits. Existing ledger
   progress counts; do not reset it. Trace a user-selected safe workload after
   mapping when available; otherwise disclose reproduction-only runtime coverage.
3. For a new audit, call `audit_queue` with `repo` and the chosen limits (defaults:
   `budget: 15`, `per_round: 6`, `max_rounds: 3`). For later or resumed rounds,
   supply only `repo` to preserve the ledger's limits unless the user changes them.
   Honor its stop condition and path warnings. Read each batch's `context_file`
   and adjacent Markdown guide once. These come from installed Argus, not Agensi.
   Follow that guide's reproduction protocol and supplied `repro_skeleton`.
   For legacy queues, use the accessible installed reproduction guide; do not
   invent a manual path.
4. Process one batch per generic investigator, at most four concurrently, or
   sequentially without subagents. Budget is per function item. Verify each
   `abs_file` against its numbered `source`; changed or missing source makes its
   findings inconclusive with `blocked_by: "wrong-tree"`.
5. Reproduce against real imports or compiled source under `.audit/repros/`.
   Replace external peers only. Complete skeletons; keep a distinct test per
   finding and emit `evidence(finding="EXACT_FINDING_ID", ...)` with actual
   measurements. A passing assertion must mean the predicted bug occurred.
6. Run completed files through `run_repro` with exact `repo` and absolute `file`,
   not pytest directly. Use `audit_repro_env` once when Python imports need the
   project's dependencies; disclose downloads. Read measurements in `repro_json`.
   After two setup failures for an item, mark it inconclusive. Missing toolchains,
   exhausted budget, and `auditor_mismatch` are blockers, not confirmation.
7. Collect a flat verdict list covering every supplied finding. Each entry needs
   exact `finding` and `verdict`: `confirmed`, `rejected`, or `inconclusive`.
   Confirmations include `hypothesis`, `repro_file`, measured `evidence`,
   `extreme_case`, and `smallest_fix`; others need a precise `reason` and
   inconclusive entries a `blocked_by`.
8. Rerun the round once with `run_repro({"repo": "EXACT_QUEUE_REPO"})`, then call
   `audit_record` with `repo` and the list as `verdicts`. A failed latest run
   invalidates prior confirmation. Repeat until stopped, then call `audit_report`.

Use tests, in-process fixtures, loopback mocks, or sandboxes. Never use `.env`
credentials, live exchanges, production databases, or destructive workloads.
Stop affected investigations when safe fixtures are unavailable. Do not change
application code, install packages globally, or claim skipped scaffolds, import
errors, compile failures, and unrelated timeouts prove bugs.

Report evidence-status counts, measured findings and smallest fixes, material
blockers, and `.audit/report.html`, `report.md`, and `report.json` paths. Count
function investigations separately from batches. Only executed finding-specific
reproductions justify proven findings. Avoid exhaustive-coverage or unmeasured
accuracy claims. Apply fixes only when the user requests that separate work.
