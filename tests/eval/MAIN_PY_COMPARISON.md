# main.py comparison follow-up (2026-10-15)

The supplied `audit-comparison-main-py/` run used marketplace commit
`35116a3`, not the stale installed plugin. Fixing plugin updates alone therefore
does not explain or repair its investigation cost. Original evidence is unchanged.
The exact target is preserved in `tests/fixtures/main_py_comparison/app/main.py`.
This case is now development data, not an independent performance holdout.

## What the recorded numbers establish

- Plain audit: 34,279 reported subagent tokens, four tool calls, 19 read-only
  observations. No reproduction was executed.
- Argus: 286,227 reported investigator tokens, 42 tool calls across six agents.
  This excludes orchestration tokens. The 8.4x ratio is a real difference in
  the recorded investigator totals, not a controlled comparison of equal tasks.
- Argus's report has 10 proven finding IDs and six conditionally observed IDs.
  Three of those observed IDs are duplicates at different SQLite calls within
  one function. The six observed IDs came from latency injection, not a new
  measurement of an organically slow service.
- The report's "15 investigated" counted finding verdicts, including a
  same-effect copy, rather than six function investigations. Those quantities
  are now separate in report metadata.

"19 vs 13 verified" is not verified recall. The plain list mixes several effects
in some entries and includes hypothetical changes in others. It needs per-effect
matching and executed proof before being scored against Argus.

## Coverage of the plain list

| Plain item | Assessment against this source and saved Argus evidence |
|---|---|
| 1. Blocking background sleep | Existing blocking rule; saved trace is conditional on injected latency. |
| 2. Job polling never stops | Already detected and reproduced. |
| 3. Blocking future waits | Already detected and reproduced with a large real batch. |
| 4. Dropped task ownership/errors | Already detected; a real task's dependency failure reached only the loop handler. |
| 5. Cache grows without eviction | Already detected and reproduced. |
| 6. Upstream error handling | Error-response caching already detected and reproduced. General HTTP exception-to-status policy is not modeled. |
| 7. Cache stampede | Already reproduced by `race-across-await`: 50 cold-key requests made 50 upstream calls. |
| 8. Query interpolation | Already detected and reproduced using the real HTTPX request builder and an in-process transport. |
| 9. Events/work queue growth | Both already reproduced. Authentication and response-size policy are separate concerns. |
| 10. Per-process state | A deployment constraint; no multi-worker run or persistence contract was supplied. No deployment-topology rule claims this. |
| 11. SQLite on the loop | Existing blocking leads. The supplied point-lookup test did not show a stall; injection establishes a conditional risk. Its proposed future thread race is not a present race. |
| 12. Health-check sleep | Existing blocking lead; saved trace uses injected latency. |
| 13. HTTP client per call | Already detected and reproduced. |
| 14. Full response buffering | Now detected as `buffered-response-slice`. The actual app downloads 1 MiB while returning 100 characters; a streamed control returns the same prefix after consuming one 4 KiB transport chunk. |
| 15. POST query parameter / validation | Requires an API input contract. Query parameters on POST and optional typing alone do not prove a bug. |
| 16. Counter race | The report itself says it is safe today. There is no await in the synchronous increment, and present callers run it on the event-loop thread. |
| 17. Wall-clock TTL | Real gap in relative-interval detection; fixed and proved on buggy/fixed source. |
| 18. Resource lifecycle | Additional lifecycle review, not an existing lifecycle rule. No shutdown reproduction was supplied. |
| 19. Task admission/backpressure | Now detected as `unbounded-background-tasks` for bare detached asyncio tasks in known FastAPI routes. Twenty sequential calls leave 20 pending tasks; an awaited control leaves zero. External admission policies still require review. |

The remaining unmodeled topics are not silently credited as caught, dismissed as
false, or added as noisy rules merely to match this list's count.

## Changes

1. Queue items in the same file/language share a bounded investigator batch:
   at most six items, 24 findings and 12,000 source characters. All items keep
   their budget slots, exact source paths and individual finding verdicts.
   Up to eight related findings stay in one function item, so the sixth
   cache hypothesis does not cause a second investigation of the same function.
2. The agent definition shrank from 22,501 to 5,208 bytes. Detailed guidance
   moved to a reference; queue emits just the relevant language setup and rule
   rows. Skills pass context-file paths instead of duplicating the full map,
   trace and manual in every agent prompt. Legacy single-item inputs still work.
3. Exact, untouched scaffolds are registered and skipped. The generic template
   also has an explicit failure. Neither an unused template nor a bare `pass`
   can become a confirmation. Edited reproduction files still execute normally.
4. Proof matching requires the exact evidence finding ID. A passing test for
   another finding in the same file is not proof. All tests tagged with that ID
   must pass. A later failed rerun invalidates a saved confirmation and allows
   the existing bounded retry.
5. Python relative wall-clock expiries are detected, including import aliases.
   Monotonic expiry, ordinary timestamps and shadowed clock objects have controls.
   Persisted epoch timestamps still need contextual judgment.
6. The follow-up adds two resource rules after running controlled reproductions:
   response slicing after eager body loading, and detached request tasks that
   outlive request admission slots. The fixed controls change the actual target
   code; the HTTP client, task runtime and SQLite operations remain real.

## Validation

The baseline plugin was exported from Git HEAD `35116a3`. Both versions mapped
the same unchanged, isolated target. The recorded trace was explicitly reused;
no fresh Python runtime trace was claimed on the local Python 3.11 environment.

| Check | Result |
|---|---|
| Static leads before / after | 21 / 22; the addition is `wallclock-interval@app.fetch_remote:81` |
| First-round function items before / after | 6 / 6, charged 6 of 15 slots |
| Investigator launches prescribed before / after | 6 / 1 batch; no original function or finding dropped |
| Deferred function items after | 0 |
| Replay of supplied completed reproductions | 10 passed; two negative checks still failed; no setup errors |
| Actual cache clock-jump bug/fix | Buggy: stale cache hit, one HTTP call. Fixed: expired cache, two HTTP calls. |

The 21/22 map counts above describe the TTL patch's original measurement.
With both later resource rules, the same target has 24 leads. These two new
rules do not turn the remaining deployment, API-policy or lifecycle observations
into verified bugs.

The two negative checks are the in-memory lookup stall assertion and the N+1
assertion over a polling loop using the same key. Their failure is not a compiler
or dependency error. The supplied unfilled counter template was excluded from
the replay; its original failure was never evidence. The new skip behavior is
tested independently with a generated starter and an edited, executable test.

Reproduction results and measured values are in
`.audit/comparison-recheck/target/.audit/repro.{json,md}`; the deterministic
before/after summary is `.audit/comparison-recheck/summary.json`.
The full core suite passed with 216 tests and 20 toolchain/platform skips.
The token scorer now separates investigator runs from function slots and
rechecks saved confirmations when current reproduction results are available.

No Kimi/Claude investigation was rerun. Bytes and agent launch counts are measured;
new token cost, model recall and quality are unmeasured. Rerun both approaches in
fresh conversations on unchanged target copies, with the same model and explicit
budget, and require executed evidence for both before making a cost/recall claim.

## CI profile follow-up

The reported macOS assertion expected one stall from `crunch(600)`, but a sampled
frame can disappear long enough to split that single call into multiple segments.
A deterministic 20ms-frame-loss case reproduces the incorrect split on frozen
`35116a3`; the patched profiler produces one 600ms stall. The existing live Node
assertion is retained, not weakened.

The fix uses exact V8 coverage paired with the same recording run and process.
It joins fragments only within one busy interval and only when that coverage
proves there was one call. Tests preserve two actual calls, separate busy
intervals, and the conservative behavior when coverage is missing, from another
run/process, or an unpaired import. CI also saves the failing Node fixture's
audit artifacts for diagnosis if timing issues recur. Local Node integrations
pass; the actual macOS GitHub runner still needs to rerun on the PR.

Final follow-up validation: 227 core tests passed with 20 platform/toolchain
skips; six scorer tests passed separately. These include both new resource
bug/fix controls and the live Node profiling integrations. Publication steps
are in `PUBLISHING.md`; no commit, push or remote CI run was performed here.

For the local regression checks:

```sh
python -m pip install -r plugins/argus/requirements.txt -r tests/fixtures/main_py_comparison/requirements.txt
python -m pytest tests/test_comparison_gaps.py tests/test_scaffold.py tests/test_orchestrate.py tests/test_repro.py -q
```
