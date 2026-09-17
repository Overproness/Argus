# Repo Auditor: Plan

An agent workflow that audits a codebase in small pieces. It finds extreme cases,
looks at how they spread through the call graph, and turns every suspicion into a
reproducible test before reporting it.

## Why this exists

Asking an LLM "what's wrong with this repo?" gets you plausible guesses. It then
fixates on them and misses real problems. For example, a synchronous exchange API
call inside an async trading loop took 30 s once and stalled everything.

This workflow reverses that. **Fixed tools list every risky point in the code. The
LLM only decides what to investigate. Every finding needs evidence.**

## Status

| Milestone | State |
|---|---|
| M1: static map (Rust) | ✅ done |
| M1.5: every major language + precise call resolution | ✅ done. 12 languages, SCIP import, checked against rattler (Rust, 457 files, 3 s) and sktime (Python, 1,111 files, 10 s) |
| M2: runtime observation + fault injection | next |
| M3: investigator agent + generated reproduction tests | planned |
| M4: parallel investigators, passing effects in both directions, final report | planned |
| M5: deeper verification (deterministic simulation, performance fuzzing, invariant mining) | planned |

## Form factor

- **Now: a Claude Code plugin.** It bundles skills, subagents, hooks (safety
  guards) and scripts.
- **Portability: keep the logic outside the plugin.** The analysis lives in a
  standalone CLI (`plugins/repo-auditor/scripts/auditor`). An MCP server comes
  next. The skills are thin `SKILL.md` files (the open Agent Skills format), so
  Codex, Gemini CLI, Cursor and others can reuse them.
- **Later:** a headless service or GitHub Action built on the Claude Agent SDK. A
  website only makes sense as a report viewer.

---

## Language support (M1.5)

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

For your trading bug, the M2/M5 path is:
1. Wrap the exchange client behind Toxiproxy, or use turmoil/madsim in tests.
2. Inject 30 s of latency on one call.
3. Assert that the strategy loop still ticks within its deadline.

That test fails on the sync call, which proves the finding.

### F. Analysis techniques the agents apply

- **Following effects to callers:** latency, blocking, panics and errors climb to
  the entry points. (M1 ✅ for blocking; M4 adds the rest.)
- **Following effects to callees:** value ranges and sizes flow downstream. For
  example, "returns ≤ 50k items" reaching an O(n²) function. (M4)
- **Timeout budgets:** a callee's timeout plus its retries must fit inside the
  caller's deadline. Otherwise the caller gives up first and the work is wasted.
- **Retry storms:** retries multiplied across fan-out and layers; missing backoff
  or jitter; non-idempotent retries.
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
  - `audit-trace`: tracing adapters for Rust (`tracing`), Python
    (`sys.monitoring`), Node (`diagnostics_channel` / `--cpu-prof`), Go (pprof),
    JVM (JFR) and .NET (EventPipe).
  - A SQLite trace store and complexity fitting.
  - A fault-injection harness (Toxiproxy on Linux/macOS, clumsy on Windows, or
    in-process mocks).
  - A safety hook.
  - Import of results from existing linters (section B) as extra evidence.
- **M3: investigator.**
  - A subagent that takes one hotspot or finding cluster and writes a
    reproduction: a property test, fuzzing harness or latency-injection test.
    It runs the reproduction and reports the evidence.
  - An MCP server that exposes `audit_map`, `audit_trace` and `run_repro`, so
    other tools can use it.
- **M4: orchestration.**
  - Parallel investigators with a budget.
  - Passing value ranges downstream and latency/failures to callers.
  - Timeout-budget and retry-storm analysis.
  - A final report (an HTML artifact).
- **M5: deep verification.**
  - Deterministic simulation (turmoil/madsim) for Rust services.
  - Performance fuzzing on hot functions.
  - Daikon-style invariant mining.
  - Kani/CrossHair on critical maths.
- **Backlog of static rules:**
  - retry without backoff;
  - unbounded retries;
  - unbounded channels and queues;
  - sequential awaits that could run concurrently;
  - spawns never joined;
  - `unwrap`/panic on external data;
  - floats for money;
  - `SystemTime` used for intervals;
  - regexes with catastrophic backtracking;
  - loading all rows without pagination.
- **Evaluation:** run on the real Rust trading repo. Known bugs such as the sync
  API call are the ground truth; measure recall and false-positive rate on each
  milestone.

## Risks

- **False positives:** reduced so far by call-resolution tiers, argument-count
  and visibility filters, and severity calibration. The verification step is the
  real fix.
- **Cost of one agent per function:** avoid it. Rank hotspots and cap the budget.
- **Realistic inputs for microservices:** record/replay, mocks and deterministic
  simulation are where most of the effort goes.
- **Unverified SCIP indexers:** only rust-analyzer is tested so far. Verify the
  others as their toolchains become available (Go isn't installed on this
  machine).
