# Validation on 2026-10-14

Frozen Argus commit: `4b0b65f`.
Plugin SHA-256: `8def8d625938c313833f9f655a9cfdc9371f40ea138050c357348b0fa5c4af66`.
No detector changes were made for this benchmark.

The generated bundle is `.audit/relaybench/`. Its mixed application target is
`targets/sample-011`; `.audit/relaybench-target.zip` is a source-only export of
that target. It contains 28 application files, with normalized timestamps and
no answer key, Git history, audit map or private oracle.

## Contract validation

**14/14 pairs validated**: each buggy snapshot violates its contract, and each
fixed snapshot satisfies the same contract. Setup errors and compiler failures
are excluded from proof. All native probes compile against the actual exported
Rust library by path, with locked dependencies and offline builds.

Full measurements: `.audit/relaybench/judge/verification.json`.
The clean Python demo and its three public smoke tests also passed; the clean
Rust application compiled and ran. Eight benchmark integrity/export/scoring
tests passed. The final repository suite passed with **196 tests and 20 skips**.

## Strict static development score

| Measurement | Result |
|---|---:|
| Canonical rule/function/file detected on buggy snapshot | 11/14 |
| Canonical finding absent after the fix | 10/14 |
| Targeted clean-control violations | 0/3 |
| Mixed target, initial top-5 queue coverage | 4/14 |
| Mixed target, initial top-10 queue coverage | 7/14 |
| Mixed target, initial top-15 queue coverage | 10/14 |

Canonical misses: `p08`, `r03`, `r04`. A related blocking-call lead for a case
is not automatically a canonical deadline-rule hit. `r02` is flagged on both
sides even though the runtime lock-wait contract clears after the fix.

Detailed maps and score: `.audit/relaybench/judge/static-score.json` and `.md`.
The runner reads the current implementation's defaults (15 total, 6 per round,
3 rounds), rather than assuming older documentation's numbers.

These are synthetic development measurements, not held-out recall, overall
precision, or LLM investigation results. The cases were authored with knowledge
of Argus, and no model investigation was run.
