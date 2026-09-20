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

**Scope: every programming language.** Argus is not built for one language or
one kind of program. Every capability (the static map, effects, runtime
evidence, reproduction, linter import) has to work for all supported languages.
A capability that works in one language only is a gap, not a feature. See
[Language parity](#language-parity).

## Status

| Milestone | State |
|---|---|
| M1: static map (Rust) | ✅ done |
| M1.5: every major language + precise call resolution | ✅ done. 12 languages, SCIP import, checked against rattler (Rust, 457 files, 3 s) and sktime (Python, 1,111 files, 10 s) |
| M2: runtime observation + fault injection | ✅ Python: tracer (`sys.monitoring`), SQLite store, stall detection, N+1 counts, complexity fitting, evidence report joined to the map, in-process fault injection, safety hook. Open: tracers for other languages, out-of-process fault injection, linter import |
| M3: investigator agent + generated reproduction tests | ✅ Python in-process; every other language through M5 P1 (fault server plus black-box runs or native probes; Rust verified end to end with real investigator subagents). `investigator` subagent, `audit-investigate` skill, in-process reproduction harness (latency/hang injection, loop-lag monitor, deadlines, scaling fits, call counts), `repro` runner, PreToolUse safety guard, MCP server (`audit_map`, `audit_trace`, `run_repro`) |
| M4: parallel investigators, passing effects in both directions, final report | ✅ Static effect engine for all 12 languages (waits, deadlines, retries, crash-on-error). Size projections from traces. Budgeted rounds with follow-ups and suppression. Verdict ledger checked against reproductions. `audit` skill, three new MCP tools, `report.html` |
| M5: language parity: every capability in every language | in progress. ✅ P1 language-neutral reproduction (fault server, evidence protocol, native probes). ✅ P2a language-neutral runtime evidence (OpenTelemetry receiver and importer, span-to-function mapping, observed external calls, heartbeat stalls with attribution). P2b native tracers: ✅ Node (V8 profile + coverage, automatic), ✅ profile import (`.cpuprofile`, speedscope); Rust, Go, JVM, .NET open. Open: P3–P5; see [Language parity](#language-parity) |
| M6: deeper verification (deterministic simulation, performance fuzzing, invariant mining) | planned |

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
| Runtime evidence | ✅ an OpenTelemetry receiver (OTLP/HTTP, protobuf and JSON, gzip) and file importer; spans mapped to map functions; client spans become observed external calls; ✅ heartbeat stalls (gaps between a program's own output lines), attributed to the deepest span covering the gap; ✅ sampling-profile import (V8 `.cpuprofile`, speedscope) | function-level tracers: Python `sys.monitoring` ✅; Node V8 profiler + coverage ✅; Rust `tracing` layer, Go runtime/trace, JVM JFR, .NET EventPipe to do |
| Linter evidence | SARIF import (one importer; most linters emit SARIF) | a table of linter commands |
| Verification of Argus itself | a CI matrix that installs every toolchain | one fixture per language per capability |

### Parity matrix

✅ verified in this repo's tests · ◐ implemented, not yet verified (toolchain missing here) · ✗ missing

| Language | Static map | Effects | Precise calls (SCIP) | Runtime evidence | Repro: black-box | Repro: native probe | Linter import |
|---|---|---|---|---|---|---|---|
| Python | ✅ | ✅ | ◐ | ✅ tracer + OTLP | ✅ | ✅ in-process | ✗ |
| Rust | ✅ | ✅ | ✅ | ◐ OTLP (SDK) | ✅ | ✅ | ✗ |
| JavaScript | ✅ | ✅ | ◐ | ✅ V8 profiler + coverage (functions, stalls, exact counts), no setup; OTLP for external calls | ✅ | ✅ | ✗ |
| TypeScript | ✅ | ✅ | ◐ | ◐ same Node profiler (runs under `tsx`/`ts-node`; transpiled lines map by function name) | ✅ | ◐ (needs `tsx`) | ✗ |
| Go | ✅ | ✅ | ◐ | ◐ OTLP (SDK) | ◐ | ◐ | ✗ |
| Java | ✅ | ✅ | ◐ | ✅ OTLP (Java agent) | ✅ | ✅ | ✗ |
| Kotlin | ✅ | ✅ | ◐ | ◐ OTLP (Java agent) | ◐ | ◐ | ✗ |
| Scala | ✅ | ✅ | ◐ | ◐ OTLP (Java agent) | ◐ | ◐ | ✗ |
| C# | ✅ | ✅ | ◐ | ◐ OTLP (.NET) | ✅ | ✅ | ✗ |
| Swift | ✅ | ✅ | ✗ | ◐ OTLP (SDK) | ◐ | ◐ | ✗ |
| C | ✅ | ✅ | ◐ | ◐ OTLP (SDK) | ✅ | ✅ | ✗ |
| C++ | ✅ | ✅ | ◐ | ◐ OTLP (SDK) | ✅ | ✅ | ✗ |
| Ruby | ✅ | ✅ | ◐ | ◐ OTLP (SDK) | ◐ | ◐ | ✗ |
| PHP | ✅ | ✅ | ◐ | ◐ OTLP (SDK) | ◐ | ◐ | ✗ |

"Runtime evidence" means OpenTelemetry spans through Argus's receiver, or a
native channel where one is named. The protocol side is verified (real Python
SDK exporter, official Java agent). A language stays ◐ until a real program in
it has been traced in the tests.

Two channels are not tied to a language, so they get no column:
- Heartbeat stalls work for any program that prints periodically (verified on
  Node and Python programs).
- Profile import reads V8 `.cpuprofile` and speedscope JSON, which py-spy
  (Python), rbspy (Ruby) and dotnet-trace (.NET) export. It is verified on
  synthetic files only.

The black-box path runs any command, so it works in every language as soon as
the program can be pointed at the fault server (an environment variable, config
file or argument for the dependency's URL). It is marked ✅ where a real
program in that language has been run and observed through `run_target` in the
tests. Rust, JavaScript, Java and C# programs were run against the fault server;
C and C++ programs were run for scaling.

### Getting to full parity (milestone M5)

- **P1 ✅ Language-neutral reproduction**: the fault server, the `@@evidence`
  protocol for any process, black-box runs, and native probes (scaffolds and run
  commands) for Rust, JS, Java, C# and C/C++. Templates exist for Go, TS,
  Kotlin, Scala, Swift, Ruby and PHP. The queue no longer skips findings by
  language.
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
    - Verified with the real OpenTelemetry Python SDK exporter and, end to end,
      with the official Java agent on an unmodified Java program (method spans
      mapped, HttpURLConnection calls observed, `io-in-loop` confirmed). `trace --otlp`
      derives the Java agent's method list (`OTEL_INSTRUMENTATION_METHODS_INCLUDE`)
      from the map's JVM findings and entry points, so no setup is needed for
      function-level spans on the JVM.
    - Also verified with Node's auto-instrumentation (`--require`), whose
      chunked uploads the receiver decodes: external calls observed, and
      findings in files without function spans reported as `not-traced`
      rather than `not-exercised`. Heartbeats verified on a Node program.
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
    - ✅ **Profile import** (`trace-import --profile`, `audit_trace_import`):
      - a V8 `.cpuprofile` (Chrome or Deno DevTools);
      - speedscope JSON, sampled or evented, the format py-spy, rbspy,
        dotnet-trace and others export. Stalls need idle samples; runs without
        them report stall rules as `not-verifiable`.

      Verified on synthetic files. The real exporters have not been run in
      the tests yet.
    - Open:
      - Rust: a `tracing` layer, or an import of Chrome trace-event JSON,
        which `tracing-chrome` writes with one event pair per poll, so a long
        poll is a stall;
      - Go: runtime/trace or pprof;
      - JVM: JFR or async-profiler;
      - .NET: verify dotnet-trace's speedscope export;
      - Ruby: verify rbspy;
      - Swift, C, C++: perf or samply.
- **P3 Linter evidence**: ✅ SARIF importer (`lint-import`, MCP `audit_lint_import`, results placed in map
  functions, corroborating equivalent map findings; tested on synthetic SARIF) and a runner for ruff,
  golangci-lint and semgrep (unverified: none installed here). Open: runners for the rest, verifying real
  linter output. Original scope: a SARIF importer plus a runner table (clippy via
  clippy-sarif, ruff, golangci-lint, eslint, detekt, Roslyn analyzers, semgrep,
  PMD/SpotBugs, RuboCop, PHPStan, SwiftLint). Imported results confirm or
  contradict map findings.
- **P4 Precise calls everywhere**: verify each SCIP indexer on a fixture; add an
  LSP call-hierarchy fallback (sourcekit-lsp for Swift).
- **P5 CI matrix**: GitHub Actions installing all toolchains and running every
  per-language fixture for every capability. This turns ◐ into ✅ and keeps it
  there.
- **Evaluation corpus**: per language, real repositories with known bugs as
  ground truth. Recall and false-positive rate per language and per capability.
  Any user repo, such as a trading bot, joins as one more ground-truth case.

---

## Language support: static map (M1.5)

Parsing uses `tree-sitter-language-pack`. Each language is one `LangSpec`: node
types, async rules, library rules and hooks.

| Language | Blocking-in-async | Language-specific checks | Libraries recognised | SCIP indexer |
|---|---|---|---|---|
| Rust | ✅ tokio; `spawn_blocking`/`block_in_place` count as offloaded | sync lock held across `.await`; calls inside macros (`select!`, `join!`, `format!`) | reqwest (blocking default 30 s), ureq, std fs/net/process, sqlx, redis, tonic | rust-analyzer ✅ verified |
| Python | ✅ asyncio; `to_thread`/`run_in_executor` count as offloaded | `with threading.Lock` around `await` | requests, httpx (5 s), aiohttp (300 s), urllib, subprocess, psycopg, Django/SQLAlchemy ORM, asyncpg, motor | scip-python |
| JS / TS | ✅ event loop | `await` in loop | fs `*Sync`, child_process, fetch, axios, got, Prisma, Mongoose, pg, knex, TypeORM, better-sqlite3 | scip-typescript |
| Go | n/a | goroutine per loop iteration; `defer` in loop; `ctx`-aware calls | net/http (DefaultClient has no timeout), database/sql, sqlx, pgx, gorm, grpc, os/exec | scip-go |
| Java | n/a | stream lambdas count as loops | java.net.http, OkHttp (10 s), RestTemplate, WebClient, JDBC, Spring Data repositories, JPA | scip-java |
| Kotlin | ✅ coroutines; `withContext(Dispatchers.IO)` counts as offloaded | `runBlocking` inside a suspend function | OkHttp, Ktor, JDBC, Spring Data | scip-java |
| Scala | ✅ inside `Future {}` / `IO {}` | `Await.result` | sttp, akka/pekko-http | scip-java |
| C# | ✅ async/await; `Task.Run` counts as offloaded | sync-over-async (`.Result`, `.Wait()`, `GetAwaiter().GetResult()`) | HttpClient (100 s), Dapper/ADO.NET, EF Core, File.* | scip-dotnet |
| Swift | ✅ async/await; `Task {}` | semaphore waits | URLSession (60 s), Alamofire, `Data(contentsOf:)` | none (no mature indexer) |
| C / C++ | n/a | `future.get()` waits | libcurl (no timeout by default), sockets, sqlite/libpq/mysql, cpr | scip-clang (needs `compile_commands.json`) |
| Ruby | n/a | iterator blocks count as loops | Net::HTTP (60 s), HTTParty, Faraday, ActiveRecord (N+1) | scip-ruby |
| PHP | n/a | none yet | curl, Guzzle (no timeout), Laravel HTTP (30 s), PDO, Eloquent (N+1) | scip-php |

"✅ verified" means tested in this repo. Other indexer command lines follow each
project's README and may need adjusting.

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
| SCIP indexers (rust-analyzer, scip-python, scip-typescript, scip-java, scip-go, scip-clang, scip-dotnet, scip-ruby, scip-php) | per language | **M1.5 ✅** precise calls |
| LSP call hierarchy (`callHierarchy/incomingCalls`) | any language with an LSP server | fallback when no SCIP indexer exists (Swift via sourcekit-lsp) |
| stack-graphs (GitHub), Kythe, Glean, LSIF | multi | alternative precise indexes |
| Call-graph algorithms (CHA, RTA, points-to), PyCG, go `callgraph` (VTA), WALA, Soot/SootUp, Doop | per language | virtual-dispatch precision |
| CodeQL | C/C++, C#, Go, Java/Kotlin, JS/TS, Python, Ruby, Swift | data flow, taint, custom queries (M3: "does this value reach that loop bound?") |
| Joern (code property graphs) | C/C++, Java, JS, Python, PHP, Kotlin | cross-function data flow queries |
| Semgrep / ast-grep | 30+ | cheap custom rules; easy for the agent to generate |

### B. Existing linters that already encode some of our rules

We import their results as extra evidence instead of re-implementing them.

| Rule area | Tools |
|---|---|
| Blocking in async | ruff/flake8-async (`ASYNC2xx`), clippy (`await_holding_lock`, `await_holding_refcell_ref`), Microsoft.VisualStudio.Threading.Analyzers (VSTHRD002/103), AsyncFixer, detekt coroutine rules, BlockHound (runtime, JVM), Ben.BlockingDetector (runtime, .NET) |
| Missing timeouts / context | golangci-lint `noctx`, `bodyclose`; ruff `S113` (requests without timeout); Semgrep rules |
| Await in loops, N+1 | eslint `no-await-in-loop`; bullet (Rails, runtime); nplusone and django-silk (Python); Laravel Debugbar/Telescope; Hibernate statistics |
| Performance lints | ruff `PERF`, Perflint, clippy `perf`, staticcheck, gocritic, SpotBugs, Error Prone, PMD, Roslyn CA18xx, SwiftLint, clang-tidy `performance-*`, cppcheck, RuboCop Performance, PHPStan/Psalm/Larastan |
| General static analysis | Infer (Meta), SonarQube, Coverity, PVS-Studio, gosec, Brakeman, Bandit, Pysa |
| Regex blow-ups (ReDoS) | recheck, regexploit, safe-regex, CodeQL `js/redos` |

### C. Input exploration (finding the extreme cases)

| Technique | Tools | Use |
|---|---|---|
| Property-based testing | Hypothesis, proptest, quickcheck, fast-check, jqwik, FsCheck/CsCheck, rapid/gopter, ScalaCheck, Kotest, PropCheck | **M3**: the agent writes properties, the tool searches for counterexamples |
| Coverage-guided fuzzing | AFL++, libFuzzer, honggfuzz, cargo-fuzz, cargo-afl, Atheris, Jazzer, Jazzer.js, native Go fuzzing, SharpFuzz; OSS-Fuzz and ClusterFuzzLite for CI | **M3**: generated harnesses, OSS-Fuzz-gen style |
| **Performance fuzzing** | PerfFuzz, SlowFuzz, HotFuzz (JVM), Singularity, Badger | **M5**: search for inputs that maximize run time or call counts, which is exactly the "extreme case" goal |
| API / protocol fuzzing | Schemathesis (OpenAPI/GraphQL), RESTler, EvoMaster, Dredd | fuzzing across service boundaries |
| Symbolic / concolic execution | KLEE, SymCC, angr, Manticore, CrossHair (Python), ExpoSE (JS), Symbolic PathFinder (Java), Pex/IntelliTest (.NET) | **M5**: the "dry run the algorithm" step for hot functions |
| Model checking / proofs | Kani (Rust), CBMC, JBMC, Prusti, Creusot, TLA+/PlusCal, P language | proving loop bounds and invariants on critical code |
| Automatic test generation | Pynguin, EvoSuite, Randoop, UTBot, Diffblue Cover, CoverUp, Cover-Agent, TestGen-LLM | seed tests for the investigator |
| Metamorphic testing | framework-free (write relations) | trading maths: "scaling all prices by k scales PnL by k", "reordering independent fills leaves the position unchanged" |
| Differential testing | compare against a reference implementation or the previous version | catches regressions in extreme cases |
| Mutation testing | cargo-mutants, mutmut, Stryker, PIT, go-mutesting, Infection, mutant | measures whether generated tests would catch real bugs |
| Minimizing failing inputs | ddmin, C-Reduce/cvise, Hypothesis/proptest shrinking | turns a failure into the smallest trigger |

### D. Runtime observation (M2)

| Language | Tracing / profiling | Detects blocking or event-loop stalls |
|---|---|---|
| Rust | `tracing` (+ tracing-chrome, tracing-timing), samply, cargo-flamegraph, perf, dhat-rs, criterion/divan, iai-callgrind, coz (causal profiling), tokio-metrics | **tokio-console** (task poll times, busy tasks) |
| Python | `sys.monitoring` (PEP 669), cProfile, py-spy, Scalene, pyinstrument, Austin, yappi, memray, viztracer | **asyncio debug mode `slow_callback_duration`**, aiomonitor |
| Node / TS | `--cpu-prof`, clinic.js (doctor/bubbleprof/flame), 0x, `diagnostics_channel`, `async_hooks` | **`perf_hooks.monitorEventLoopDelay`**, blocked-at |
| Go | pprof (CPU, heap, **block**, **mutex**), runtime/trace, httptrace, fgprof, benchstat | block/mutex profiles, goleak |
| JVM | JFR + JMC, async-profiler, JMH, Arthas, VisualVM | **BlockHound**, JFR thread-park events |
| .NET | dotnet-trace, dotnet-counters, dotnet-monitor, PerfView, BenchmarkDotNet | **thread-pool starvation counters**, Ben.BlockingDetector |
| C / C++ | perf, eBPF (bpftrace, BCC `offcputime`), uftrace, LLVM XRay, `-finstrument-functions`, Valgrind (callgrind, massif), Tracy, VTune, sanitizers (ASan, UBSan, TSan) | off-CPU analysis |
| Ruby | stackprof, rbspy, ruby-prof, TracePoint, rack-mini-profiler | bullet (N+1) |
| PHP | Xdebug, SPX, Excimer, Blackfire, Tideways | Debugbar/Telescope query counts |
| Swift | Instruments (Time Profiler, Swift Concurrency, System Trace), `os_signpost` | Thread Performance Checker, hang detection |
| Any language | OpenTelemetry (auto-instrumentation for Java, .NET, Python, Node, Go via eBPF/Beyla), Pyroscope/Parca continuous profiling, Jaeger/Tempo | trace critical-path analysis |
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
| Record once, replay later | vcrpy, pytest-recording, VCR (Ruby), Polly.JS, go-vcr, WireMock recording, mitmproxy, GoReplay, Speedscale |
| Failpoints inside code | `fail` crate (Rust), pingcap/failpoint (Go), Byteman (JVM) |
| **Deterministic simulation** | **turmoil** and **madsim** (tokio), shuttle and loom (Rust concurrency), Coyote (.NET), Lincheck (Kotlin/JVM), FoundationDB-style simulation, Antithesis |
| Distributed correctness | Jepsen/Elle, Porcupine (linearizability) |
| Trading-specific | exchange testnets and paper accounts (Binance testnet, Coinbase sandbox, Alpaca paper, IBKR paper); market-data tick replay; event-driven backtesters with latency models (**nautilus_trader**, **hftbacktest**, Lean, backtrader) |
| Load and stress | k6, Locust, Gatling, wrk2, vegeta, oha, goose, Artillery, JMeter |

The same recipe works in any language. For example, for the motivating bug (a
sync exchange call in an async trading loop):
1. Point the exchange client at Argus's fault server (or Toxiproxy, or a
   deterministic simulator such as turmoil/madsim for Rust or Coyote for .NET).
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
  - Adapters still to write: Rust (`tracing` layer writing the same tables),
    Node (`diagnostics_channel` / `--cpu-prof`), Go (pprof), JVM (JFR), .NET
    (EventPipe). Each reads `AUDIT_TRACE_*` from `trace/run.py`.
  - Fault injection ✅ in-process for Python (`repro/harness.py`: socket-level
    latency and hangs). Toxiproxy/clumsy for out-of-process targets: to do.
  - Safety hook ✅ (`hooks/hooks.json`, `scripts/hooks/guard.py`).
  - Import of results from existing linters (section B) as extra evidence.
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
  - `.mcp.json` + `scripts/mcp_server.py`: `audit_map`, `audit_trace`, `run_repro`.
  - Still to do: property-based and fuzzing reproductions (needs Hypothesis
    templates), non-Python harnesses.
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
      `retry-amplification`, `panic-on-io-error` (Rust `unwrap`/`expect`,
      Swift `try!`), `ignored-io-error` (Go `_`).
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
    `fault-server` and `probe` CLI commands):
    - `FaultServer`: a mock HTTP API or TCP proxy with `latency`, `hang`,
      `reset` and `fail_first`. It records each connection's time and peer, and
      the gaps between connections.
    - `run_target()`: runs any command and observes it from outside: exit code,
      wall time, whether it returned before the deadline, stdout lines with
      arrival times, `@@evidence` lines, and heartbeat gaps. It kills the whole
      process tree on timeout.
    - Native probe scaffolds: a side project per language that depends on the
      repo by path, plus a small evidence helper in that language.
  - P2a ✅ Language-neutral runtime evidence (`trace/otlp.py`, `trace/spans.py`,
    `trace --otlp`, `trace --heartbeat`, `trace-import`):
    - an OTLP/HTTP receiver and decoders, sharing a hand-written protobuf
      reader with the SCIP import (`protowire.py`);
    - a `Mapper` from spans to map functions;
    - `IoRec` external calls in the trace store, and an "External calls
      observed" section in `trace.md`;
    - heartbeat logs with stall attribution.

    `evidence.py` needed no change for stalls: attributed heartbeat stalls use
    the same stack format as the Python tracer's.
  - P2b, partly done (`trace/profiles.py`, `trace/sources.py`, `trace --node`,
    `trace-import --profile/--coverage`, MCP `audit_trace_import`):
    - readers for V8 `.cpuprofile` and speedscope (sampled and evented), and
      V8 coverage as exact counts (`CountRec` in the trace store);
    - `evidence.py` knows sampled runs. It never counts calls from samples.
      It confirms N+1 findings from count ratios, and reports sampled-only
      rules (recursion depth, complexity fits) as `not-verifiable`;
    - a function with no map entry of its own (a closure, a module's top-level
      code) is kept apart from map functions that share its lines;
    - processes that never ran repo code (npm itself) are dropped;
    - `Mapper.rel` no longer maps `node_modules/x/index.js` to the repo's
      `index.js` by suffix.

    Native tracers for Rust, Go, JVM and .NET are still open.
  - P3 SARIF linter import, P4 SCIP/LSP everywhere, P5 CI matrix, evaluation
    corpus: open.
- **M6: deep verification**, in every language:
  - deterministic simulation (turmoil/madsim for Rust, Coyote for .NET,
    Lincheck for the JVM, simulated clocks elsewhere);
  - performance fuzzing on hot functions (PerfFuzz-style, per language via its
    fuzzer: cargo-fuzz, Atheris, Jazzer, Go fuzzing, SharpFuzz);
  - Daikon-style invariant mining;
  - proofs and symbolic checks on critical maths (Kani, CrossHair, JBMC, KLEE).
- **Backlog of static rules:**
  - ✅ retry without backoff, unbounded retries, retry amplification (M4);
  - ✅ `unwrap`/`expect` on network and DB results (M4). Open: panics on parsed
    external data (JSON fields, index access);
  - ✅ (heuristic, source patterns in `langs/patterns.py`, tested on Rust and
    Python): backoff without jitter, unbounded channels and queues, sequential
    awaits, spawns never joined, floats for money, `SystemTime`/wall clock used for
    intervals, regexes with nested repeats, loading all rows, panics on parsed
    data (Rust, Go, Swift, Kotlin), CPU-heavy work on an async path (nested loops
    with no await). Open: non-idempotent retries (POST without an idempotency
    key); retry libraries configured by call (tokio-retry, `backoff::retry`,
    retry-go, Polly, p-retry); per-language tuning of the patterns on real code
    (false-positive rate unmeasured).
- **Evaluation:** a corpus of real repositories per language with known bugs as
  ground truth. Measure recall and false-positive rate per language and per
  capability on each milestone. Any user repo (a trading bot with the sync API
  call, say) is one more ground-truth case, not the target.

## Risks

- **False positives:** reduced so far by call-resolution tiers, argument-count
  and visibility filters, and severity calibration. The verification step is the
  real fix.
- **Cost of one agent per function:** avoid it. Rank hotspots and cap the budget.
- **Realistic inputs for microservices:** record/replay, mocks and deterministic
  simulation are where most of the effort goes.
- **Unverified languages:** several toolchains (Go, Ruby, PHP, Swift, Kotlin,
  Scala) are not installed on the development machine, so their SCIP indexers,
  native probes and black-box runs are implemented but unverified. The CI matrix
  (P5) is the fix; until then the parity matrix says ◐, not ✅.
- **One-language drift:** new features tend to land in the language at hand
  first. Every new capability needs a language-neutral path, or an entry in the
  parity matrix saying which languages lack it.
