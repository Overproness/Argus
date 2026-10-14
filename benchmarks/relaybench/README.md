# Relaybench

A runnable delivery service and Tokio edge observer for testing Argus's
static map, initial queue ranking, and evidence-producing investigations.
It contains **14 isolated bug/fix pairs** (10 Python, 4 Rust), a clean target,
and a mixed target with all 14 bugs. It generates actual Git history with
one-purpose fixes; each buggy commit is the immediate parent of its fix.

This is a **synthetic development benchmark**, created with knowledge of
Argus's implementation. It is useful for regression testing and comparing
investigators on a frozen workload. It cannot provide an independent held-out
estimate of performance on real repositories. The application is deliberately
small; there are no padding files to make it appear larger.

## What ships

- `baseline/`: complete runnable application, normal service documentation,
  smoke tests, Python standard-library dependencies and a locked Tokio crate.
- `judge/`: seed definitions and application-contract oracles. Keep private
  from the auditing model.
- `build.py`: exports 30 opaque source-only targets and private Git history.
- `verify.py`: checks the same contract on each pair. Every buggy version
  must violate it and every fixed version must satisfy it.
- `score.py`: maps the targets with frozen Argus and measures strict canonical
  rule recall, whether the finding clears on the fix, targeted controls, and
  initial top-5/10/15 queue coverage.
- `pack.py`: packages just one target for an investigator, without answers,
  Git history, maps or generated reproductions.

## Build and validate

Run from the Argus repository root. Python 3.11+ and Git are sufficient to
build the dataset and verify its Python cases. Full verification also needs
Cargo/Rust; validated here with Rust 1.91.1.

```sh
python benchmarks/relaybench/build.py --out .audit/relaybench
python benchmarks/relaybench/verify.py --bundle .audit/relaybench
```

Rust dependencies are locked. Fetch them once if your Cargo cache is empty:

```sh
cargo fetch --manifest-path benchmarks/relaybench/baseline/edge/Cargo.toml --locked
```

Verification is offline by default and compiles a native probe that depends
on the real target crate by path. Both languages use loopback peers, isolated
temporary storage, counted dependency operations, or real synchronization
primitives. Source code under test is imported/compiled, never rewritten by
the oracle. Python's dispatcher uses an injected adapter to count concurrent
deliveries; authorization uses a delayed callback to expose overlap.

Or verify just Python:

```sh
python benchmarks/relaybench/verify.py --bundle .audit/relaybench --language python
```

The verifier distinguishes a contract violation from a setup/compilation error.
It does not count a compiler failure, bad import, or process timeout as a
reproduced bug. Results and measured effects go into `judge/verification.json`.
Timing cases have generous margins; a noisy machine that fails a control needs
investigation, not a passing score manufactured by changing the threshold.

Build refuses to overwrite an existing bundle. Choose a new output directory
for a new version. Source hashes, oracle hashes, and the Argus plugin hash at
creation are recorded in `judge/manifest.json`.

## Test the map and queue

Install Argus's usual requirements, then:

```sh
python benchmarks/relaybench/score.py --bundle .audit/relaybench
```

`judge/static-score.md` and `.json` contain the results. The runner reads
Argus's actual budget defaults from the implementation; it does not assume
the numbers in CLI help or older documentation are accurate.

Scoring matches a predeclared canonical rule, function and file. A different
related warning may still help an investigator, but is not credited as a
canonical hit. Findings that survive the fix are reported separately. Extra
unlabelled findings are **not automatically false positives**, so this score
does not claim overall precision. Three targeted clean controls check a local
lookup loop, exact decimal money, and fixed-size nested iteration.

Mixed-target queue coverage counts distinct bug IDs, grouped investigations
and same-effect coverage. It is an initial ranking measurement, not a simulation
of later rounds after verdict feedback. The low-severity event-buffer seed
also tests whether the default severity filter hides a real effect.

The detector is not changed to make this suite pass. Some cases intentionally
exercise practical syntax variations such as `handle.spawn(...)`, multiline
await assignments, and scoped mutex guards.

## Give a model one target

As the evaluator, read `judge/manifest.json` to select the mixed or an isolated
sample. The `targets/sample-*` names do not disclose its case or side.

```sh
python benchmarks/relaybench/pack.py --bundle .audit/relaybench --sample sample-000 --out .audit/agent-target.zip
```

Replace `sample-000` with the chosen ID from the manifest. The ZIP contains
only the application. Keep `judge/`, the benchmark source folder, fix history,
oracle results, and scores outside the model's accessible environment. A sibling
folder or an instruction saying "don't read this" is not an access boundary.
If transferring Argus too, transfer its plugin directory rather than this
entire workspace, which contains the answer key.

For comparisons, start each run with a fresh unpacked target, the same model
and explicit investigation budget, and a new conversation. Run a plain audit
on another identical copy as a baseline. Do not repair application source
during an audit; reproductions belong under `.audit/repros/`. Save the
generated tests and verdicts. Self-reported confirmations are not ground truth:
the evaluator must check whether a reproduction exercises the actual source
and whether its observed effect changes on the fixed snapshot.

The supplied contract oracles fail on buggy sources and pass on fixed sources.
An Argus reproduction that asserts the *presence* of a bug has the opposite
pass/fail orientation; either orientation can be valid, but must be declared
before replaying the same test on both snapshots.

## Extend without contaminating a holdout

Use this suite as development data. For an actual holdout, freeze Argus and
have a separate author add new cases with their own fix diffs and executable
contracts, without inspecting detector output. After using a case for tuning,
move it into development data. Secrecy of an answer key alone does not make a
case independently authored. Real historical fixes should be evaluated as a
separate corpus from these seeds.
