# Token totals: Argus vs plain prompt on app/main.py

Figures are the per-subagent `subagent_tokens` totals reported by the harness when each agent finished.
The Argus map, trace, queue, record and report steps are deterministic Python and use no LLM tokens.

## Plain prompt

| Run | Tokens | Tool calls | Duration |
|---|---|---|---|
| plain audit (1 agent) | 34,279 | 4 | 43.1 s |

## Argus (latest marketplace HEAD 35116a3, queue round 1, budget 15)

| Investigator (function) | Findings settled | Tokens | Tool calls | Duration |
|---|---|---|---|---|
| fetch_remote | 5 | 63,160 | 8 | 79.5 s |
| get_job | 3 | 47,986 | 8 | 61.6 s |
| create_job | 2 | 46,344 | 8 | 201.7 s |
| run_batch | 2 | 43,390 | 6 | 36.3 s |
| record_request | 1 | 42,691 | 6 | 171.5 s |
| process_job | 1 | 42,656 | 6 | 154.7 s |
| **Total** | **14** | **286,227** | **42** | investigators ran up to 4 in parallel |

Deterministic steps (0 LLM tokens): static map 0.3 s, trace with latency injection 2.7 s,
repro-env 108 s (one-off dependency install), repro rerun, record, report.

## Comparison

| | Tokens | Relative |
|---|---|---|
| Plain prompt | 34,279 | 1.0x |
| Argus (investigators only) | 286,227 | 8.4x |

Per distinct issue: plain 19 findings (unverified) is about 1.8k tokens each; Argus 13 distinct verified
issues is about 22k tokens each.

## Not included

- Main-session orchestration cost for the Argus run (launching agents, reading results, running the CLI steps).
  The session's context counter fell by about 67k tokens over the whole task, but that figure also covers the
  version check and the plain audit's report, so it is not attributable to either run.
- Each Argus investigator reads a 22 KB instruction file (`investigator.md`) before starting, which is a fixed
  cost per investigator.
- One run of each approach only, so there is no variance estimate.
