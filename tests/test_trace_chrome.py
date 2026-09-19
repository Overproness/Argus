"""Chrome trace-event import (Rust tracing-chrome): B/E per poll, mapped to the rust_trader fixture."""
import json
import subprocess
import sys

import pytest
from conftest import FIXTURES, ROOT

CLI = ROOT / "plugins" / "argus" / "scripts" / "auditor_cli.py"
FIX = FIXTURES / "rust_trader"


def span(name, file, line, t0, t1, tid=1):
    a = {"file": file, "line": line} if file else {}
    return [{"ph": "B", "name": name, "ts": t0 * 1e6, "pid": 7, "tid": tid, "args": a},
            {"ph": "E", "name": name, "ts": t1 * 1e6, "pid": 7, "tid": tid}]


def trace_events():
    ev = []
    # main polls 0..0.6 s; inside it run_strategy -> compute_signal -> fetch_price blocks 0.3 s (a sync call)
    ev += span("main", "src/main.rs", 10, 0.0, 0.6)[:1]
    ev += span("run_strategy", "src/strategy.rs", 16, 0.1, 0.55)[:1]
    ev += span("compute_signal", None, None, 0.15, 0.5)[:1]
    ev += span("Exchange::fetch_price", "src/exchange.rs", 13, 0.2, 0.5)
    ev += span("compute_signal", None, None, 0.15, 0.5)[1:]
    ev += span("run_strategy", None, None, 0.1, 0.55)[1:]
    ev += span("tokio::runtime::park", None, None, 0.55, 0.56)  # unmapped library span
    ev += span("main", None, None, 0.0, 0.6)[1:]
    # refresh_all calls fetch_book 3 times within one activation
    ev += span("refresh_all", "src/market_data.rs", 18, 1.0, 1.03)[:1]
    for i in range(3):
        ev += span("fetch_book", "src/market_data.rs", 5, 1.0 + i * 0.01, 1.005 + i * 0.01)
    ev += span("refresh_all", None, None, 1.0, 1.03)[1:]
    return ev


@pytest.fixture(scope="module")
def report(tmp_path_factory):
    out = tmp_path_factory.mktemp("audit")
    f = out / "trace.json"
    f.write_text(json.dumps({"traceEvents": trace_events()}))
    subprocess.run([sys.executable, CLI, "map", FIX, "--out", out], check=True, capture_output=True)
    r = subprocess.run([sys.executable, CLI, "trace-import", FIX, "--chrome", f, "--out", out],
                       check=True, capture_output=True, text=True)
    assert "1 chrome trace(s)" in r.stdout and "7 calls" in r.stdout
    return json.loads((out / "trace.json").read_text())


def finding(report, rule, function, line):
    for f in report["findings"]:
        if (f["rule"], f["function"], f["line"]) == (rule, function, line):
            return f["evidence"]
    raise AssertionError(f"no finding {rule} {function}:{line}")


def test_blocking_call_on_async_stack_confirmed(report):
    ev = finding(report, "blocking-in-async", "rust_trader::strategy::run_strategy", 20)
    assert ev["status"] == "confirmed"
    assert "fetch_price" in ev["detail"] and "run_strategy" in ev["detail"]


def test_direct_sleep_is_an_unpredicted_or_confirmed_stall_on_main(report):
    ev = finding(report, "blocking-in-async", "rust_trader::main", 15)
    assert ev["status"] == "confirmed"


def test_call_counts_for_n_plus_one(report):
    ev = finding(report, "io-in-loop", "rust_trader::market_data::refresh_all", 20)
    assert ev["status"] == "confirmed" and "3×" in ev["detail"]


def test_unmapped_library_spans_are_transparent(report):
    assert all("park" not in f["function"] for f in report["functions"])


def test_short_slices_are_not_stalls(report):
    fns = {f["function"]: f for f in report["functions"]}
    assert fns["rust_trader::market_data::fetch_book"]["stalls"] == 0
