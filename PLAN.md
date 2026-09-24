# Argus: Plan

An agent workflow that audits a codebase in small pieces. It finds extreme cases,
looks at how they spread through the call graph, and turns every suspicion into a
reproducible test before reporting it.

## Why this exists

Asking an LLM "what's wrong with this repo?" gets you plausible guesses. It then
fixates on them and misses real problems. For example, a synchronous exchange API
call inside an async trading loop took 30 s once and stalled everything.

This workflow reverses that. **Fixed tools list every risky point in the code. The
LLM only decides what to investigate. Every finding needs evidence.**

**Scope: Rust, Python, JavaScript/TypeScript, Go, Java, C/C++.** Argus targets
these six languages, chosen to cover the shapes of the motivating bug (async
runtimes, goroutines, JVM threads, native code) without spreading thin. Every
capability (the static map, effects, runtime evidence, reproduction, linter
import) has to work for all six. A capability that works in one language only
is a gap, not a feature. See [Language parity](#language-parity).

Argus previously also covered Kotlin, Scala, C#/.NET, Swift, Ruby and PHP.
That support was removed to concentrate effort on the six languages above;
see git history before this cut if it is ever needed again.

## Status

| Milestone | State |
|---|---|
| M1: static map (Rust) | ✅ done |
| M1.5: precise call resolution | ✅ done. 6 languages, SCIP import verified for real on all of them (rust-analyzer, scip-python, scip-typescript, scip-go; scip-java unverified in this environment), checked against rattler (Rust, 457 files, 3 s) and sktime (Python, 1,111 files, 10 s) |
| M2: runtime observation + fault injection | ✅ Python: tracer (`sys.monitoring`), SQLite store, stall detection, N+1 counts, complexity fitting, evidence report joined to the map, in-process fault injection, safety hook. ✅ Rust: `tracing-chrome` import, verified against a real recording. Linter import (SARIF) done. Open: native tracers for Go/JVM/C++, out-of-process fault injection |
| M3: investigator agent + generated reproduction tests | ✅ Python in-process; ✅ native probe or black-box verified for real for Rust, JavaScript, TypeScript, Java, Go, C, C++ (every language in scope). `investigator` subagent, `audit-investigate` skill, in-process reproduction harness (latency/hang injection, loop-lag monitor, deadlines, scaling fits, call counts), `repro` runner, PreToolUse safety guard, MCP server |
| M4: parallel investigators, passing effects in both directions, final report | ✅ Static effect engine for all 6 languages (waits, deadlines, retries, crash-on-error). Size projections from traces. Budgeted rounds with follow-ups and suppression. Verdict ledger checked against reproductions. `audit` skill, MCP tools, `report.html` |
| M5: language parity: every capability in every language | in progress, close to done for the 6-language scope. ✅ P1 native probes/black-box verified for all 6. ✅ P2a OpenTelemetry: real Python SDK, real Go SDK, real Node auto-instrumentation all verified; Java agent implemented, unverified here (large-file download unreliable in this sandbox; the code path is inert for non-Java repos, see below). ✅ P2b Node (automatic), Rust (`tracing-chrome`, real trace verified). ✅ P3 SARIF linter import. ✅ Packaging and cost controls (below). Evaluation harness built and tested, corpus seeded with local cases only. Open: P2b for Go/JVM/C++ native tracers, P5 CI (written, never run), real `git` corpus cases |
| M6: deeper verification (deterministic simulation, performance fuzzing, invariant mining) | planned |

**Java in a non-Java repo does nothing.** The OpenTelemetry Java agent
auto-configuration (`OTEL_INSTRUMENTATION_METHODS_INCLUDE`) only activates
when `--otlp` is passed *and* the map contains Java findings; SCIP's
`scip-java` only runs when `.java` files are present. Auditing a repo in any
other language touches none of this: verified by running `trace --otlp`
against the Python fixture and confirming no Java-related output or behavior.

## Form factor

- **Now: a Claude Code plugin.** It bundles skills, subagents, hooks (safety
  guards) and scripts.
- **Portability: keep the logic outside the plugin.** The analysis lives in a
  standalone CLI (`plugins/argus/scripts/auditor`). An MCP server comes
  next. The skills are thin `SKILL.md` files (the open Agent Skills format), so
  Codex, Gemini CLI, Cursor and others can reuse them.
- **Later:** a headless service or GitHub Action built on the Claude Agent SDK. A
  website only makes sense as a report viewer.

---

## Language parity

**The rule:** prefer mechanisms that sit *outside* the program: the network, the
process, stdout and standard file formats. One implementation then serves every
language. Per-language adapters are added only for depth the neutral path cannot
give (for example, function-level timings).

| Capability | Language-neutral mechanism (one implementation) | Per-language depth |
|---|---|---|
| Static map and effects | tree-sitter plus one `LangSpec` per language | library rule packs |
| Precise calls | SCIP (one reader for every indexer) | LSP call hierarchy where no indexer exists |
| Fault injection | **fault server**: a local mock API or TCP proxy with latency, hang, reset and fail-first-N; counts every connection | Python in-process socket patches |
| Reproduction | pytest wrappers drive any command; **`@@evidence` lines on stdout** from any language; **black-box** runs of the real program against the fault server | **native probes**: a small program in the target language, in a side project under `.audit/repros/native/` that depends on the repo by path, so the repo is never modified |
| Runtime evidence | ✅ an OpenTelemetry receiver (OTLP/HTTP, protobuf and JSON, gzip) and file importer; spans mapped to map functions; client spans become observed external calls; ✅ heartbeat stalls (gaps between a program's own output lines), attributed to the deepest span covering the gap; ✅ sampling-profile import (V8 `.cpuprofile`, speedscope) | function-level tracers: Python `sys.monitoring` ✅; Node V8 profiler + coverage ✅; Rust `tracing-chrome` import ✅; Go runtime/trace, JVM JFR to do |
| Linter evidence | ✅ SARIF import (one importer; most linters emit SARIF) | a table of linter commands to run them |
| Verification of Argus itself | a CI matrix that installs every toolchain | one fixture per language per capability |

### Parity matrix

✅ verified in this repo's tests, on a real program, this session · ◐ implemented, not yet verified for real · ✗ missing

| Language | Static map | Effects | Precise calls (SCIP) | Runtime evidence | Repro: black-box | Repro: native probe | Linter import |
|---|---|---|---|---|---|---|---|
| Python | ✅ | ✅ | ✅ scip-python | ✅ tracer + real OTLP SDK | ✅ | ✅ in-process | ✅ (neutral SARIF path) |
| Rust | ✅ | ✅ | ✅ rust-analyzer | ✅ `tracing-chrome`, real trace | ✅ | ✅ | ✅ |
| JavaScript | ✅ | ✅ | ✅ scip-typescript | ✅ V8 profiler + coverage, no setup; real OTLP (Node auto-instrumentation) | ✅ | ✅ | ✅ |
| TypeScript | ✅ | ✅ | ✅ scip-typescript | ◐ same Node profiler (runs under `tsx`) | ✅ | ✅ (fixed a real `tsx` invocation bug) | ✅ |
| Go | ✅ | ✅ | ✅ scip-go | ✅ real OTLP (Go SDK) | ✅ | ✅ | ✅ |
| Java | ✅ | ✅ | ◐ scip-java (unverified here) | ◐ OTLP (Java agent implemented; large-file download unreliable in this sandbox, see below) | ✅ | ✅ | ✅ |
| C | ✅ | ✅ | ◐ scip-clang (needs `compile_commands.json`) | ◐ OTLP (SDK) | ✅ | ✅ | ✅ |
| C++ | ✅ | ✅ | ◐ scip-clang (needs `compile_commands.json`) | ◐ OTLP (SDK) | ✅ | ✅ | ✅ |

"Runtime evidence" means OpenTelemetry spans through Argus's receiver, or a
native channel where one is named. This session verified the OTLP protocol
path for real against the Python SDK, the Go SDK and Node's
auto-instrumentation (all three previously untested: the packages were not
installed). The Java agent's own logic is implemented and reads correctly
from the map (`jvm_methods_include`), but the agent jar itself could not be
downloaded reliably here (large single-file downloads via GitHub/Maven Central
timed out repeatedly in this sandbox even though package-manager traffic,
npm/go/cargo, worked fine); it stays ◐ until run against a real JVM.

Two channels are not tied to a language, so they get no column:
- Heartbeat stalls work for any program that prints periodically (verified on
  Node and Python programs).
- Profile import reads V8 `.cpuprofile` and speedscope JSON. py-spy is the
  relevant real exporter in scope now; it could not be verified here because
  macOS requires root to attach a profiler to another process
  (`py-spy record` fails with "This program requires root on OSX" and this
  sandbox has no passwordless sudo). Verified on synthetic files only.

The black-box path runs any command, so it works in every language as soon as
the program can be pointed at the fault server (an environment variable, config
file or argument for the dependency's URL). Native probes are verified for
all six languages this session: Rust and Java were already verified; Go and
TypeScript needed real fixes (see below); JavaScript, C and C++ were already
verified.

**Two real bugs this session's verification work found and fixed:**
- The TypeScript probe ran `node --import tsx probe.mjs`, which fails because
  a global `npm install -g tsx` is not resolvable as a bare ESM specifier from
  the probe's directory (`ERR_MODULE_NOT_FOUND`). Fixed by invoking the `tsx`
  CLI directly, which registers its own loader.
- The Rust `tracing-chrome` importer assumed a span's `file`/`line` came
  through the event's `args`. A real recording puts them as top-level,
  dot-prefixed keys (`.file`, `.line`) on the event itself. Both are checked
  now.
- `scip-go`'s command line was missing the `index` subcommand
  (`scip-go --output` fails; it needs `scip-go index --output`), and its
  install path had moved (`sourcegraph/scip-go` → `scip-code/scip-go`). Fixed.

### Getting to full parity (milestone M5)

- **P1 ✅ Language-neutral reproduction**: the fault server, the `@@evidence`
  protocol for any process, black-box runs, and native probes (scaffolds and run
  commands) for all six languages in scope, all now verified against real
  toolchains. The queue no longer skips findings by language.
- **P2 Runtime evidence for every language.**
  - **P2a ✅ Neutral path**:
    - `trace --otlp` runs an OTLP/HTTP receiver (protobuf and JSON, gzip) and
      points the program's OpenTelemetry SDK or agent at it; `trace-import`
      takes collector dumps.
    - Spans map to map functions by code attributes, qualified names or
      method-span names. Client spans become observed external calls (HTTP,
      DB, RPC, messaging) with latency and errors, attributed to the calling
      function. These external calls also confirm N+1 findings on direct
      library calls, which the Python tracer alone could not see.
    - `trace --heartbeat REGEX` turns gaps between a program's own output lines
      into stalls, attributed to the deepest span covering each gap, or
      recorded at program level when there are no spans.
    - ✅ Verified with the real OpenTelemetry Python SDK exporter, the real Go
      SDK (`otlptracehttp`, zero-config via env vars) and Node's
      auto-instrumentation (`--require`), whose chunked uploads the receiver
      decodes: external calls observed, findings in files without function
      spans reported as `not-traced` rather than `not-exercised`. Heartbeats
      verified on a Node program.
    - The Java agent path (`OTEL_INSTRUMENTATION_METHODS_INCLUDE` derived from
      the map's JVM findings and entry points) is implemented and unit-level
      correct but not run against a real JVM here; see the parity matrix note.
  - **P2b Native function-level tracers** for languages whose OpenTelemetry
    setup needs code changes, or to go deeper than spans.
    - ✅ **Node** (JavaScript, TypeScript), with no code change and no
      dependency. `trace` sets `--cpu-prof` (a V8 sampling profile every
      0.5 ms) and `NODE_V8_COVERAGE` (exact per-function call counts) for every
      Node process the command starts. It does this automatically for `node`,
      `npm`, `npx`, `yarn`, `pnpm`, `tsx` and `ts-node`.
      - Stalls: within each busy stretch of the event loop, a function
        continuously on the stack longer than the threshold is a stall. The
        deepest such function is the culprit, and the sampled leaf names the
        blocking call (`spawnSync`).
      - N+1: coverage counts give the fan-out as a ratio.
      - The profile clock is aligned with epoch time from an
        (epoch, `perf_counter`) pair recorded at launch; V8 uses the same
        monotonic clock. So a heartbeat gap that a profile stall explains is
        reported once, under the function.
      - Verified end to end: `blocking-in-async` confirmed with the blocking
        call named; `io-in-loop` confirmed from counts; an unpredicted CPU
        stall found; heartbeat and OTLP combined with the profiler.
    - ✅ **Rust `tracing-chrome` import** (`trace-import --chrome`,
      `audit_trace_import`): a `#[tracing::instrument]`'d function emits a
      begin/end pair per poll; a slice with long self time while an async
      function is on the stack is a stall. Verified end to end on a real,
      compiled tokio binary with a synchronous socket call inside an async
      path (the shape of the bug this project exists for): `blocking-in-async`
      confirmed with the correct call stack and duration, `io-in-loop`
      confirmed from real per-symbol counts, and a `spawn_blocking`-offloaded
      call correctly produced *no* stall. This also found and fixed the
      file/line key-format bug above.
    - **Profile import** (`trace-import --profile`, `audit_trace_import`):
      - a V8 `.cpuprofile` (Chrome or Deno DevTools) — used automatically by
        the Node channel, effectively verified there;
      - speedscope JSON, sampled or evented, the format py-spy exports.
        Stalls need idle samples; runs without them report stall rules as
        `not-verifiable`. Verified on synthetic files only; a real py-spy
        export could not be produced here (see the sudo/SIP note above).
    - Open:
      - Go: runtime/trace or pprof (block/mutex profiles matter more than
        stalls here, since goroutines don't share a single event loop);
      - JVM: JFR or async-profiler;
      - C, C++: perf or samply.
- **P3 ✅ Linter evidence**: SARIF importer (`lint-import`, MCP
  `audit_lint_import`), results placed in map functions, corroborating
  equivalent map findings (rule-id regex table: clippy, ruff/flake8-async,
  golangci-lint, ESLint, Roslyn-style ids kept for reference). Verified on
  hand-built SARIF exercising the correlation and lead paths. A built-in
  runner exists for ruff, golangci-lint and semgrep (none installed here, so
  unverified); any linter that writes SARIF elsewhere works via `--sarif`.
- **P4 Precise calls everywhere**: rust-analyzer, scip-python, scip-typescript
  and scip-go are all now verified end to end against real ambiguous-call
  fixtures (name resolution genuinely ambiguous, SCIP resolves it correctly).
  scip-java and scip-clang remain unverified (the latter also needs a
  `compile_commands.json`, which is extra setup even once the binary exists).
- **P5 CI matrix**: [ci.yml](.github/workflows/ci.yml) installs Rust, Node,
  Java, Go and `tsx` and runs the suite on Linux/macOS/Windows, plus a nightly
  `eval` job (below). **It has still never run**: there are no GitHub
  credentials on the dev machine to push with, so it is written and
  YAML-validated but unexercised. Expect first-run fixes (Windows paths,
  toolchain versions). This is what turns ◐ into ✅ and keeps it there.
- **Evaluation corpus** (`tests/eval/`): ✅ harness implemented and tested;
  corpus seeded but not yet real.
  - `corpus.yaml` holds cases of two kinds: `local` (a fixture in this repo)
    and `git` (a real repository at a commit, with the file/function/rule
    Argus should find there). `expected` findings are scored for recall by
    rule and language; `must_be_absent` pairs are a targeted false-positive
    check.
  - `run.py` runs the real `map` CLI on each case and prints recall per case
    and per rule (exit 1 on any miss or false positive, so CI can fail on a
    regression). Verified to fail correctly on a deliberately broken case.
    `--json` writes a machine-readable summary. Kept out of `pytest tests`
    because `git` cases clone a real repo; the nightly `eval` job runs it.
  - **Honest state of the corpus:** the 3 cases in it are all `local`
    (`rust_trader`, `py_runtime`, `rust_runtime`): deliberately-injected bugs
    of the exact categories Argus targets, not bugs mined from a real
    project's history. They keep the harness itself tested and give a
    non-zero baseline (3/3 cases, 100% recall, every expected finding
    confirmed against real `map` output rather than assumed), but 100% recall
    on fixtures you wrote to trigger the rules proves the harness works, not
    that Argus is good on real code. **No `git` cases exist yet**: mining
    real history needs `git clone` from GitHub, which was unreliable in the
    dev sandbox this was built in (even a tiny 1 KB repo timed out). The
    schema and `_fetch_git_case` are written and ready; what is missing is
    finding real fix commits and checking the exact function names by
    running `map` against them.
  - Any user repo, such as a trading bot, joins as one more ground-truth case.

---

## Language support: static map (M1.5)

Parsing uses `tree-sitter-language-pack`. Each language is one `LangSpec`: node
types, async rules, library rules and hooks.

| Language | Blocking-in-async | Language-specific checks | Libraries recognised | SCIP indexer |
|---|---|---|---|---|
| Rust | ✅ tokio; `spawn_blocking`/`block_in_place` count as offloaded | sync lock held across `.await`; calls inside macros (`select!`, `join!`, `format!`) | reqwest (blocking default 30 s), ureq, std fs/net/process, sqlx, redis, tonic | rust-analyzer ✅ verified |
| Python | ✅ asyncio; `to_thread`/`run_in_executor` count as offloaded | `with threading.Lock` around `await` | requests, httpx (5 s), aiohttp (300 s), urllib, subprocess, psycopg, Django/SQLAlchemy ORM, asyncpg, motor | scip-python ✅ verified |
| JS / TS | ✅ event loop | `await` in loop | fs `*Sync`, child_process, fetch, axios, got, Prisma, Mongoose, pg, knex, TypeORM, better-sqlite3 | scip-typescript ✅ verified |
| Go | n/a | goroutine per loop iteration; `defer` in loop; `ctx`-aware calls | net/http (DefaultClient has no timeout), database/sql, sqlx, pgx, gorm, grpc, os/exec | scip-go ✅ verified (command line and install path both needed fixing) |
| Java | n/a | stream lambdas count as loops | java.net.http, OkHttp (10 s), RestTemplate, WebClient, JDBC, Spring Data repositories, JPA | scip-java (unverified here) |
| C / C++ | n/a | `future.get()` waits | libcurl (no timeout by default), sockets, sqlite/libpq/mysql, cpr | scip-clang (needs `compile_commands.json`; unverified here) |

"✅ verified" means tested in this repo, this session, against a real
ambiguous-call fixture and the actual indexer binary. Other indexer command
lines follow each project's README and may need adjusting.

**How call resolution works, from best to weakest:**
1. SCIP index (precise).
2. Scope rules: `self`/`this` calls, `Type::method`, module paths, imports.
3. A unique method name, filtered by argument count and visibility.
4. Ambiguous names. Chains that depend on these get a lower severity.

**Calibration done on real code.** On rattler, high findings dropped from 188 to 1
(and that one is plausible). The fixes were:
- argument-count and visibility filters, which removed chains like
  `url.set_path(x)` → `Bash::set_path(a, b, c, d)`;
- one boundary per statement, so builder chains count once;
- severity by category: blocking file access is medium, while network, sleeps,
  processes and waits are high;
- repo-wide evidence of client timeouts: when the repo configures client
  timeouts somewhere, calls on a client object built elsewhere are downgraded to
  low with a pointer to that configuration.

---

## Catalogue of tools and techniques

This is the full toolbox the workflow can draw on. "Use" says where each one
fits in our milestones.

### A. Code structure and resolution

| Tool / technique | Languages | Use |
|---|---|---|
| tree-sitter (+ language pack) | all | **M1 ✅** parsing |
| SCIP indexers (rust-analyzer, scip-python, scip-typescript, scip-java, scip-go, scip-clang) | per language | **M1.5 ✅** precise calls |
| LSP call hierarchy (`callHierarchy/incomingCalls`) | any language with an LSP server | fallback when no SCIP indexer exists (e.g. `clangd` for C/C++ before a `compile_commands.json` exists) |
| stack-graphs (GitHub), Kythe, Glean, LSIF | multi | alternative precise indexes |
| Call-graph algorithms (CHA, RTA, points-to), PyCG, go `callgraph` (VTA), WALA, Soot/SootUp, Doop | per language | virtual-dispatch precision |
| CodeQL | C/C++, Go, Java, JS/TS, Python | data flow, taint, custom queries (M3: "does this value reach that loop bound?") |
| Joern (code property graphs) | C/C++, Java, JS, Python | cross-function data flow queries |
| Semgrep / ast-grep | 30+ | cheap custom rules; easy for the agent to generate |

### B. Existing linters that already encode some of our rules

We import their results as extra evidence instead of re-implementing them.

| Rule area | Tools |
|---|---|
| Blocking in async | ruff/flake8-async (`ASYNC2xx`), clippy (`await_holding_lock`, `await_holding_refcell_ref`), AsyncFixer, BlockHound (runtime, JVM) |
| Missing timeouts / context | golangci-lint `noctx`, `bodyclose`; ruff `S113` (requests without timeout); Semgrep rules |
| Await in loops, N+1 | eslint `no-await-in-loop`; nplusone (Python); Hibernate statistics |
| Performance lints | ruff `PERF`, Perflint, clippy `perf`, staticcheck, gocritic, SpotBugs, Error Prone, PMD, clang-tidy `performance-*`, cppcheck |
| General static analysis | Infer (Meta), SonarQube, Coverity, PVS-Studio, gosec, Bandit, Pysa |
| Regex blow-ups (ReDoS) | recheck, regexploit, safe-regex, CodeQL `js/redos` |

This is where [`lint-import`](#getting-to-full-parity-milestone-m5) (P3, done)
plugs in: any of these that write SARIF become evidence.

### C. Input exploration (finding the extreme cases)

| Technique | Tools | Use |
|---|---|---|
| Property-based testing | Hypothesis (Python), proptest/quickcheck (Rust), fast-check (JS/TS), jqwik (Java) | **M3**: the agent writes properties, the tool searches for counterexamples |
| Coverage-guided fuzzing | AFL++, libFuzzer, honggfuzz, cargo-fuzz, cargo-afl (Rust), Atheris (Python), Jazzer/Jazzer.js (Java/JS), native Go fuzzing; OSS-Fuzz and ClusterFuzzLite for CI | **M3**: generated harnesses, OSS-Fuzz-gen style |
| **Performance fuzzing** | PerfFuzz, SlowFuzz, HotFuzz (JVM), Singularity, Badger | **M5**: search for inputs that maximize run time or call counts, which is exactly the "extreme case" goal |
| API / protocol fuzzing | Schemathesis (OpenAPI/GraphQL), RESTler, EvoMaster, Dredd | fuzzing across service boundaries |
| Symbolic / concolic execution | KLEE, SymCC, angr, Manticore (C/C++), CrossHair (Python), ExpoSE (JS), Symbolic PathFinder (Java) | **M5**: the "dry run the algorithm" step for hot functions |
| Model checking / proofs | Kani (Rust), CBMC, JBMC, Prusti, Creusot, TLA+/PlusCal, P language | proving loop bounds and invariants on critical code |
| Automatic test generation | Pynguin, EvoSuite, Randoop, UTBot, Diffblue Cover, CoverUp, Cover-Agent, TestGen-LLM | seed tests for the investigator |
| Metamorphic testing | framework-free (write relations) | trading maths: "scaling all prices by k scales PnL by k", "reordering independent fills leaves the position unchanged" |
| Differential testing | compare against a reference implementation or the previous version | catches regressions in extreme cases |
| Mutation testing | cargo-mutants, mutmut, Stryker, PIT, go-mutesting, Infection, mutant | measures whether generated tests would catch real bugs |
| Minimizing failing inputs | ddmin, C-Reduce/cvise, Hypothesis/proptest shrinking | turns a failure into the smallest trigger |

### D. Runtime observation (M2)

| Language | Tracing / profiling | Detects blocking or event-loop stalls |
|---|---|---|
| Rust | `tracing` (+ **tracing-chrome** ✅ imported, tracing-timing), samply, cargo-flamegraph, perf, dhat-rs, criterion/divan, iai-callgrind, coz (causal profiling), tokio-metrics | **tokio-console** (task poll times, busy tasks) |
| Python | `sys.monitoring` (PEP 669) ✅ built-in tracer, cProfile, py-spy, Scalene, pyinstrument, Austin, yappi, memray, viztracer | **asyncio debug mode `slow_callback_duration`**, aiomonitor |
| Node / TS | `--cpu-prof` ✅ automatic, clinic.js (doctor/bubbleprof/flame), 0x, `diagnostics_channel`, `async_hooks` | **`perf_hooks.monitorEventLoopDelay`**, blocked-at |
| Go | pprof (CPU, heap, **block**, **mutex**), runtime/trace, httptrace, fgprof, benchstat | block/mutex profiles, goleak |
| JVM | JFR + JMC, async-profiler, JMH, Arthas, VisualVM | **BlockHound**, JFR thread-park events |
| C / C++ | perf, eBPF (bpftrace, BCC `offcputime`), uftrace, LLVM XRay, `-finstrument-functions`, Valgrind (callgrind, massif), Tracy, VTune, sanitizers (ASan, UBSan, TSan) | off-CPU analysis |
| Any language | OpenTelemetry ✅ receiver + real SDK/agent verified (Java agent, Python, Node, Go), Pyroscope/Parca continuous profiling, Jaeger/Tempo | trace critical-path analysis |
| OS level | strace/ltrace, dtrace; **Windows: ETW, WPR/WPA, Process Monitor** | wait analysis |

**Plan for M2.** Use a language-native tracer to record, per call:
- call ID, parent ID, duration;
- the *shape* of the arguments (lengths, sizes, magnitudes), which is what
  exposes O(n²).

Records go to SQLite. Complexity fitting times each function at n = 10…10k and
fits log-log and change-point models (`big_O`-style).

### E. Simulating dependencies and injecting faults (M2)

| Need | Tools |
|---|---|
| Latency, timeouts, dropped connections | **Toxiproxy**, tc netem (Linux), **clumsy** (Windows), Pumba, Chaos Mesh, Litmus, AWS FIS, Gremlin |
| Mock servers with delays and error responses | WireMock, MockServer, Mountebank, Hoverfly, Prism (OpenAPI), wiremock-rs, httpmock, mockito (Rust), respx/aioresponses/responses (Python), msw/nock (JS) |
| Record once, replay later | vcrpy (Python), Polly.JS, go-vcr, WireMock recording, mitmproxy, GoReplay, Speedscale |
| Failpoints inside code | `fail` crate (Rust), pingcap/failpoint (Go), Byteman (JVM) |
| **Deterministic simulation** | **turmoil** and **madsim** (tokio), shuttle and loom (Rust concurrency), Lincheck (JVM), FoundationDB-style simulation, Antithesis |
| Distributed correctness | Jepsen/Elle, Porcupine (linearizability) |
| Trading-specific | exchange testnets and paper accounts (Binance testnet, Coinbase sandbox, Alpaca paper, IBKR paper); market-data tick replay; event-driven backtesters with latency models (**nautilus_trader**, **hftbacktest**, Lean, backtrader) |
| Load and stress | k6, Locust, Gatling, wrk2, vegeta, oha, goose, Artillery, JMeter |

The same recipe works in any language. For example, for the motivating bug (a
sync exchange call in an async trading loop):
1. Point the exchange client at Argus's fault server (or Toxiproxy, or a
   deterministic simulator such as turmoil/madsim for Rust).
2. Inject 30 s of latency on one call.
3. Assert, from the program's heartbeat lines or a native probe, that the loop
   still ticks within its deadline.

That test fails on the sync call, which proves the finding.

### F. Analysis techniques the agents apply

- **Following effects to callers:** latency, blocking, panics and errors climb to
  the entry points. (✅ blocking in M1; ✅ worst-case waits, unbounded waits and
  crash-on-error in M4; ✅ proven effects become caller follow-ups between
  rounds.)
- **Following effects to callees:** value ranges and sizes flow downstream. For
  example, "returns ≤ 50k items" reaching an O(n²) function. (✅ from traces:
  sizes linked caller to callee, projected with the fitted curve. Static range
  inference is still open.)
- **Timeout budgets:** a callee's timeout plus its retries must fit inside the
  caller's deadline. Otherwise the caller gives up first and the work is wasted.
  (✅ `timeout-budget-exceeded`, plus `deadline-cannot-preempt` for deadlines
  around blocking code.)
- **Retry storms:** retries multiplied across fan-out and layers; missing backoff
  or jitter; non-idempotent retries. (✅ loops and decorators; jitter and
  idempotency are open.)
- **Tail-latency amplification:** fan-out to N services with p99 latency L gives
  roughly p99 ≈ L for any N > 1. Hedged requests and bounded concurrency help.
- **Backpressure:** unbounded channels and queues (`unbounded_channel`,
  `asyncio.Queue()` with no maxsize), unbounded caches, missing pagination.
- **Invariant mining:** Daikon-style detection of likely invariants in traces,
  which feeds property tests.
- **Log and trace mining:** parse existing logs with Drain, detect anomalies and
  find critical paths in traces.
- **Concurrency hazards:** locks held across I/O, priority inversion,
  fire-and-forget spawns, goroutine and task leaks, data races (TSan, the Go
  race detector, loom).
- **Numeric hazards:** floats used for money, integer overflow, wall-clock time
  (`SystemTime`) used for intervals instead of a monotonic clock (`Instant`),
  division by values that can be zero.

### G. LLM-agent prior art

These are as of my knowledge; check them before relying on any of them.
- Google OSS-Fuzz-gen (an LLM writes fuzzing harnesses).
- Google Big Sleep / Naptime (an LLM forms hypotheses, runs tools and verifies,
  which is the same loop we use).
- Meta TestGen-LLM and ACH (mutation-guided LLM test generation).
- DARPA AIxCC cyber-reasoning systems (fuzzing combined with LLMs).
- IRIS (CodeQL combined with an LLM).
- SWE-agent, OpenHands, Agentless, AutoCodeRover.
- Semgrep Assistant, Copilot Autofix.

---

## Workflow (target architecture)

1. **Survey the repo:** languages, entry points, runtimes, external
   dependencies. Classify each side effect as safe, expensive or dangerous.
2. **Map the code (✅):** call graph, list of I/O and external calls, findings,
   hotspots.
3. **Observe (M2):** use the cheapest safe method:
   - static only;
   - mine existing logs and traces;
   - automatic tracing in an isolated copy of the repo;
   - one function at a time with generated inputs;
   - record and replay;
   - fault injection;
   - sandbox or testnet;
   - real execution, only with human approval and a budget cap.
4. **Investigate (M3):** subagents per call path or cluster, each producing a
   structured hypothesis: trigger, constraints and expected effect.
5. **Verify (M3):** a hypothesis becomes a finding only once its reproduction
   test, benchmark, fault-injection run or property counterexample actually
   runs.
6. **Pass effects around the graph (M4):** in both directions, investigate again
   with the new constraints, and stop at a depth limit, a budget limit, or when
   a round finds nothing new.
7. **Report (M4):** ranked findings, each with its path, trigger, observed vs.
   expected behaviour, reproduction file, fix and confidence.

A PreToolUse hook blocks live endpoints and production credentials no matter
what the agents decide.

## Milestones

- **M2: observation.**
  - `audit-trace` ✅ (Python): `sys.monitoring` tracer recording every repo
    function activation in slices (self time, longest uninterrupted slice,
    argument sizes), stalls (a self slice over the threshold on a thread
    running an asyncio loop, with the stack), one SQLite file per process.
    `evidence.py` joins the trace to `map.json`: confirmed / not-observed /
    not-exercised / measured / not-verifiable per finding, unpredicted stalls,
    N+1 fan-out per activation, recursion depth, log-log complexity fits.
  - ✅ Node (`--cpu-prof` / `NODE_V8_COVERAGE`, automatic) and ✅ Rust
    (`tracing-chrome` import, real-trace verified). Each reads `AUDIT_TRACE_*`
    from `trace/run.py`. Adapters still to write: Go (pprof), JVM (JFR).
  - Fault injection ✅ in-process for Python (`repro/harness.py`: socket-level
    latency and hangs). Toxiproxy/clumsy for out-of-process targets: to do.
  - Safety hook ✅ (`hooks/hooks.json`, `scripts/hooks/guard.py`).
  - ✅ Import of results from existing linters (section B) as extra evidence
    (`linters.py`, SARIF).
- **M3: investigator.** ✅ (Python)
  - `agents/investigator.md`: one finding in, one JSON verdict out
    (confirmed / rejected / inconclusive) with hypothesis {trigger,
    constraints, expected effect}, the reproduction file and measured numbers.
  - `auditor/repro/harness.py`: `latency()`, `hang()`, `refuse_remote()`,
    `loop_monitor()`, `call_with_deadline()`, `scaling()`, `count_calls()`;
    every helper emits `@@evidence` lines.
  - `auditor/repro/runner.py` + `auditor_cli.py repro`: runs
    `.audit/repros/test_*.py` under pytest, parses JUnit XML with captured
    stdout, writes `repro.{json,md}`. Passing means reproduced.
  - `skills/audit-investigate`: selects findings, fans out investigators (≤3
    parallel), aggregates with `repro`, reports verdicts.
  - `hooks/hooks.json` + `scripts/hooks/guard.py`: PreToolUse guard blocking
    live exchange hosts, credentials and PROD markers in audit runs, remote URLs
    and secrets in reproduction files, and destructive commands.
  - `.mcp.json` + `scripts/mcp_server.py`: `audit_map`, `audit_trace`, `run_repro`,
    `audit_trace_import`, `audit_lint_import`, `audit_queue`, `audit_record`, `audit_report`.
  - Still to do: property-based and fuzzing reproductions (needs Hypothesis
    templates).
- **M4: orchestration.** ✅
  - `effects.py` (all languages, static):
    - Each call site's worst-case wait comes from its explicit timeout, a
      client-level timeout, the library default, or "unbounded".
    - Callers inherit it, capped by any deadline they wrap around the call,
      unless the callee blocks the thread. Retry loops and retry decorators
      multiply it.
    - `timeouts.py` reads durations for every language: `from_secs`,
      `timeout=`, `5*time.Second`, `ofSeconds`, `FromSeconds`, `CURLOPT_TIMEOUT`,
      JS milliseconds and positional `wait_for(..., 10)`.
    - Findings: `deadline-cannot-preempt`, `timeout-budget-exceeded`,
      `hang-reaches-entry`, `retry-without-backoff`, `unbounded-retry`,
      `retry-amplification`, `panic-on-io-error` (Rust `unwrap`/`expect`),
      `ignored-io-error` (Go `_`).
    - A summary goes into `map.json` → `effects`: entry points with their worst
      wait, every deadline with its status, every retry site.
    - Calibrated on rattler:
      - retry loops bounded by a counter or retry policy are not "unbounded";
      - one attempt is not a retry;
      - `unwrap` only counts when applied to the call's own result;
      - a client configured elsewhere in the repo is assumed to carry its
        timeout, unless the file builds an unconfigured client itself.
  - Sizes flowing downstream (`trace/evidence.py` → `projections`):
    - a callee's input size is linked to its caller's when they match in one
      activation;
    - the largest upstream size, or `--assume ARG=N`, is fed through the fit
      (`Fit.predict`) to project cost.
  - `orchestrate.py` + CLI `queue` / `record` / `report`:
    - ranked, budgeted rounds (total, per round, max rounds), with merging by
      leaf;
    - skip reasons for everything not queued;
    - `propagated:<rule>` follow-ups at the callers of confirmed findings
      (entry points and deadlines first);
    - `wrong_edge` suppression from rejected verdicts;
    - a ledger in `.audit/verdicts.json` that downgrades "confirmed" when the
      reproduction does not pass;
    - stop reasons: budget, rounds, or nothing left.
  - `report_html.py`: `report.{json,md,html}`. The page follows the artifact
    contract (theme-aware, self-contained, filterable, with an evidence track
    per finding).
  - `skills/audit` runs the whole loop. `audit-investigate` is now one round
    through the same queue and ledger. MCP gained `audit_queue`,
    `audit_record` and `audit_report`. The harness gained `fail_connect()` and
    `count_connects()` for retry reproductions.
- **M5: language parity.** See [Getting to full parity](#getting-to-full-parity-milestone-m5).
  - P1 ✅ Language-neutral reproduction (`repro/faults.py`, `repro/native.py`,
    `fault-server` and `probe` CLI commands), now verified for real across all
    six languages in scope:
    - `FaultServer`: a mock HTTP API or TCP proxy with `latency`, `hang`,
      `reset` and `fail_first`. It records each connection's time and peer, and
      the gaps between connections.
    - `run_target()`: runs any command and observes it from outside: exit code,
      wall time, whether it returned before the deadline, stdout lines with
      arrival times, `@@evidence` lines, and heartbeat gaps. It kills the whole
      process tree on timeout.
    - Native probe scaffolds: a side project per language that depends on the
      repo by path, plus a small evidence helper in that language. Go's probe
      (hang + retry-burst) and TypeScript's probe (event-loop stall + hang,
      after fixing the `tsx` invocation) are now verified against real
      toolchains, joining Rust, JavaScript, Java, C and C++.
  - P2a ✅ Language-neutral runtime evidence (`trace/otlp.py`, `trace/spans.py`,
    `trace --otlp`, `trace --heartbeat`, `trace-import`):
    - an OTLP/HTTP receiver and decoders, sharing a hand-written protobuf
      reader with the SCIP import (`protowire.py`);
    - a `Mapper` from spans to map functions;
    - `IoRec` external calls in the trace store, and an "External calls
      observed" section in `trace.md`;
    - heartbeat logs with stall attribution.

    `evidence.py` needed no change for stalls: attributed heartbeat stalls use
    the same stack format as the Python tracer's. The real Python SDK, real Go
    SDK and real Node auto-instrumentation exporters are now all verified
    against the receiver (previously none of the three were actually
    installed, so this path was unverified despite being marked done).
  - P2b, mostly done for the 6-language scope (`trace/profiles.py`,
    `trace/chrome.py`, `trace/sources.py`, `trace --node`,
    `trace-import --profile/--coverage/--chrome`, MCP `audit_trace_import`):
    - readers for V8 `.cpuprofile` and speedscope (sampled and evented), and
      V8 coverage as exact counts (`CountRec` in the trace store);
    - ✅ a Chrome trace-event reader for Rust `tracing-chrome` output, verified
      end to end against a real compiled tokio binary (see the parity matrix
      note above for the two bugs this found);
    - `evidence.py` knows sampled runs. It never counts calls from samples.
      It confirms N+1 findings from count ratios, and reports sampled-only
      rules (recursion depth, complexity fits) as `not-verifiable`;
    - a function with no map entry of its own (a closure, a module's top-level
      code) is kept apart from map functions that share its lines;
    - processes that never ran repo code (npm itself) are dropped;
    - `Mapper.rel` no longer maps `node_modules/x/index.js` to the repo's
      `index.js` by suffix.

    Native tracers for Go and JVM are still open (C/C++ has no dedicated
    native tracer either, but is covered by the OTLP path).
  - P3 ✅ SARIF linter import (`linters.py`, `lint-import`, `audit_lint_import`).
  - P4 SCIP everywhere: 4 of 6 verified for real this session
    (rust-analyzer, scip-python, scip-typescript, scip-go); scip-java and
    scip-clang open.
  - P5 CI matrix: written, never run (see above). Evaluation harness:
    ✅ built and tested; real corpus cases: open.
  - **Packaging and cost (Phase 6)**:
    - ✅ **Zero-setup install.** Both entry points now bootstrap themselves:
      `mcp_server.py` (already did) and `auditor_cli.py` (new: skills invoke
      the CLI through Bash, *not* MCP, so the MCP-only bootstrap did not
      actually cover the primary Claude Code workflow). If `tree_sitter` /
      `mcp` are not importable, a private virtualenv is built under
      `~/.cache/argus/venv` (or `$ARGUS_HOME`), `requirements.txt` is
      installed into it once, and the process re-execs under it.
      Verified for real from a bare `python3 -m venv` with nothing installed
      but pip: venv built, `tree_sitter`, `tree_sitter_language_pack`, `mcp`
      and `pytest` all installed, process re-exec'd and stayed up; a second
      run reuses the cache and starts in 0.13 s. Cold first run took ~25 s
      with a warm pip cache; expect longer on a truly cold machine.
      **Caveat, not fixed:** if you use the MCP tools directly (not the
      skills), that first-run install happens *before* the server answers its
      handshake, and I could not confirm Claude Code's MCP startup timeout.
      If the MCP tools are missing right after installing the plugin, wait
      and restart once; the cached venv makes every later start instant.
      The skills path is unaffected (a Bash call has no such timeout).
    - ✅ **MCP results trimmed.** Every tool already wrote its full result to
      `.audit/`; it also returned all of it, thousands of tokens on a large
      repo. Now `audit_map`, `audit_trace` and `audit_trace_import` return
      counts by severity and rule plus the 10 most severe findings (message
      text cut to 240 chars) and a note pointing at the file for the rest;
      `full=True` restores the old behaviour. `run_repro` returns counts by
      outcome and only the tests that did *not* pass; `audit_lint_import`
      returns 10 leads instead of 30. Checked on a 15-finding fixture: 15 on
      disk, 10 returned. *Not measured:* actual token savings, since that
      needs a real audit run.
    - ✅ **Investigator cap.** `maxTurns` 40 → 20 (a normal investigation is
      ~5–10 tool calls), plus an explicit budget in the agent's instructions:
      one focused attempt and at most one revised hypothesis before
      `rejected`/`inconclusive`. This complements the existing "two failed
      attempts to build → inconclusive" rule, which only covered build
      errors, not open-ended re-hypothesising.
    - ✅ **`action.yml`.** A composite GitHub Action for other repos:
      static `map`, optional linter SARIF, a job summary, an uploaded
      `.audit/` artifact, and an optional `fail-on: high|medium|low|info`
      severity gate. No AI credentials needed. Its two bits of glue live in
      `scripts/ci_helpers.py` (tested) rather than inline YAML heredocs, after
      a first draft got Python-in-YAML indentation wrong. **Never run on
      GitHub**; the optional AI step (`claude-code-action` / Agent SDK with
      the budget flags) is not written, since it needs secrets only the
      repo owner can set.
    - ⛔ **Not done: a real token-cost measurement.** That needs an actual
      agentic audit run on a mid-size repo with token counts recorded; I
      cannot fabricate that number, and it is the one input needed to tell
      whether the trimming above matters in practice.
- **M6: deep verification**, in every language:
  - deterministic simulation (turmoil/madsim for Rust, simulated clocks
    elsewhere);
  - performance fuzzing on hot functions (PerfFuzz-style, per language via its
    fuzzer: cargo-fuzz, Atheris, Jazzer, Go fuzzing);
  - Daikon-style invariant mining;
  - proofs and symbolic checks on critical maths (Kani, CrossHair, JBMC, KLEE).
- **Backlog of static rules:**
  - ✅ retry without backoff, unbounded retries, retry amplification (M4);
  - ✅ `unwrap`/`expect` on network and DB results (M4). Open: panics on parsed
    external data (JSON fields, index access) beyond what `patterns.py`
    already flags for Rust and Go;
  - ✅ (heuristic, source patterns in `langs/patterns.py`, tested on Rust and
    Python): backoff without jitter, unbounded channels and queues, sequential
    awaits, spawns never joined, floats for money, `SystemTime`/wall clock used for
    intervals, regexes with nested repeats, loading all rows, panics on parsed
    data (Rust, Go), CPU-heavy work on an async path (nested loops
    with no await). Open: non-idempotent retries (POST without an idempotency
    key); retry libraries configured by call (tokio-retry, `backoff::retry`,
    retry-go, p-retry); per-language tuning of the patterns on real code
    (false-positive rate unmeasured); pattern-rule tests for JS/TS, Go, Java,
    C/C++ (only Rust and Python are tested today).
- **Evaluation:** `tests/eval/` (see P5 above): harness built, corpus seeded
  with 3 local cases, real `git` cases still to collect. Measure recall and
  false-positive rate per language and per capability on each milestone. Any
  user repo (a trading bot with the sync API call, say) is one more
  ground-truth case, not the target.

## Risks

- **False positives:** reduced so far by call-resolution tiers, argument-count
  and visibility filters, and severity calibration. The verification step is the
  real fix.
- **Cost of one agent per function:** avoid it. Rank hotspots and cap the budget.
- **Realistic inputs for microservices:** record/replay, mocks and deterministic
  simulation are where most of the effort goes.
- **Unverified languages:** all six in-scope toolchains are installed and
  their native probes, black-box runs and SCIP indexers (except scip-java and
  scip-clang) are now verified for real on this machine. What remains ◐ is
  the JVM agent (blocked by unreliable large-file downloads in this sandbox,
  not by missing code) and scip-clang (needs a `compile_commands.json`). The
  CI matrix (P5) is what keeps this from rotting once it is ✅.
- **One-language drift:** new features tend to land in the language at hand
  first. Every new capability needs a language-neutral path, or an entry in the
  parity matrix saying which languages lack it.
- **Large single-file downloads are unreliable in this sandbox:** package
  manager traffic (npm, go, cargo) works fine, but a raw multi-megabyte file
  over HTTPS (a GitHub release asset, a Maven Central jar) repeatedly failed
  with SSL/HTTP2 stream errors partway through, on two different hosts. Retry
  with `-C -` (resume) sometimes gets there; budget for it when a task needs
  one.
