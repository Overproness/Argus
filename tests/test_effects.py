"""M4 static effects: waits, deadlines, retries and crashes propagated across the call graph."""
import pytest
from conftest import FIXTURES

from auditor.analysis import RepoMap
from auditor.timeouts import parse_default, parse_duration, sleep_duration

FX = FIXTURES / "effects"


@pytest.fixture(scope="module")
def maps():
    return {lang: RepoMap(FX / lang).load() for lang in ("rust", "python", "go")}


def got(m, rule):
    return {(f.function, f.line): f for f in m.findings if f.rule == rule}


@pytest.mark.parametrize("text,lang,want", [
    ('timeout(Duration::from_secs(2), quote("BTC"))', "rust", 2),
    ("tokio::time::timeout(Duration::from_millis(500), f()).await", "rust", 0.5),
    ("requests.get(url, timeout=30).json()", "python", 30),
    ("await asyncio.wait_for(asyncio.to_thread(lambda: f(url)), 10)", "python", 10),
    ("async with asyncio.timeout(3):", "python", 3),
    ("await axios.get('u', { timeout: 2000 })", "typescript", 2),
    ("fetch(u, { signal: AbortSignal.timeout(1500) })", "javascript", 1.5),
    ("ctx, cancel := context.WithTimeout(ctx, 5*time.Second)", "go", 5),
    ("HttpRequest.newBuilder().timeout(Duration.ofSeconds(10))", "java", 10),
    (".connectTimeout(10, TimeUnit.SECONDS)", "kotlin", 10),
    ("withTimeout(5000) { fetch() }", "kotlin", 5),
    ("cts.CancelAfter(TimeSpan.FromSeconds(3))", "csharp", 3),
    ("curl_easy_setopt(c, CURLOPT_TIMEOUT, 5L);", "c", 5),
    ("$client->post('u', ['timeout' => 5])", "php", 5),
    ("req.timeoutInterval = 12", "swift", 12),
    ("no timeout here", "python", None),
])
def test_parse_duration(text, lang, want):
    assert parse_duration(text, lang) == want


def test_defaults_and_sleeps():
    assert parse_default("30s (reqwest::blocking default)") == 30
    assert parse_default("300s total (aiohttp)") == 300
    assert parse_default(None) is None
    assert sleep_duration("time.sleep(0.12)", "python") == 0.12
    assert sleep_duration("Thread.sleep(100)", "java") == 0.1
    assert sleep_duration("time.Sleep(time.Second)", "go") == 1


def test_rust_deadline_cannot_preempt_blocking(maps):
    f = got(maps["rust"], "deadline-cannot-preempt")[("rust::feed::tick", 23)]
    assert f.severity == "high"
    assert f.chain == ["rust::feed::tick", "rust::feed::quote", "rust::feed::price_blocking", "http @ src/feed.rs:7"]
    assert ("rust::feed::tick", 25) not in got(maps["rust"], "deadline-cannot-preempt")  # async call: fine


def test_rust_budget_retries_times_client_timeout(maps):
    f = got(maps["rust"], "timeout-budget-exceeded")[("rust::orders::place", 32)]
    assert "up to 50 s (5 attempts × 10 s)" in f.message and "deadline is 20 s" in f.message
    assert "offloaded thread keeps running" in f.message


def test_rust_retries(maps):
    m = maps["rust"]
    assert ("rust::orders::submit", 10) in got(m, "retry-without-backoff")  # MAX_ATTEMPTS const resolved
    amp = got(m, "retry-amplification")[("rust::orders::submit_all", 20)]
    assert "up to 15 attempts" in amp.message and "(3 × 5 across 2 retry layers)" in amp.message
    assert ("rust::orders::submit_all", 21) not in got(m, "retry-without-backoff")  # exponential sleep


def test_rust_hang_and_panics(maps):
    m = maps["rust"]
    hang = got(m, "hang-reaches-entry")[("rust::main", 5)]
    assert hang.chain[-1] == "http @ src/feed.rs:12 (no timeout)"
    assert set(got(m, "panic-on-io-error")) == {("rust::feed::price_blocking", 7), ("rust::feed::price_async", 12)}
    entry = m.effects["entries"][0]
    assert entry["function"] == "rust::main" and entry["max_wait"] == "unbounded"
    statuses = {(d["callee"], d["status"]) for d in m.effects["deadlines"]}
    assert statuses == {("rust::feed::quote", "cannot-preempt"), ("rust::feed::price_async", "ok"),
                        ("rust::orders::submit", "exceeded")}


def test_python_effects(maps):
    m = maps["python"]
    assert ("svc.client.guarded", 35) in got(m, "deadline-cannot-preempt")
    budget = got(m, "timeout-budget-exceeded")[("svc.client.handler", 27)]
    assert "2.0 min" in budget.message and "deadline is 10 s" in budget.message
    amp = got(m, "retry-amplification")[("svc.client.sync_all", 17)]
    assert "up to 12 attempts" in amp.message
    assert ("svc.client.fetch_retrying", 13) in got(m, "retry-without-backoff")  # tenacity without wait
    unb = got(m, "unbounded-retry")[("svc.client.poll_forever", 39)]
    assert unb.severity == "medium"  # it sleeps between attempts
    assert not any(f.function == "svc.client.process_all" for f in m.findings if "retry" in f.rule)


def test_go_effects(maps):
    m = maps["go"]
    assert ("price", 10) in got(m, "ignored-io-error")
    assert ("send", 15) in got(m, "retry-without-backoff")
    assert ("main", 29) in got(m, "hang-reaches-entry")
    assert not any(f.function == "withDeadline" for f in m.findings if f.rule == "io-without-timeout")


def test_tick_loops_are_not_retries(maps):
    # rust main's `loop {}` and go main's `for {}` poll forever but are service loops, not retries.
    for lang in ("rust", "go"):
        assert not any(f.rule == "unbounded-retry" and f.function.endswith("main") for f in maps[lang].findings)
