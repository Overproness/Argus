"""Rust runtime tracing, verified against a real `tracing-chrome` recording (not a hand-built one).

Builds and runs tests/fixtures/rust_runtime: a tokio binary with a synchronous HTTP call
(the exact shape of the bug this project exists for) reachable from an async path, one call
offloaded to spawn_blocking (should not be flagged), and an N+1 loop. Confirms that the real
tracing-chrome JSON format (file/line as top-level dot-prefixed keys, not under `args`, which
the importer originally got wrong) is read correctly end to end.
"""
import json
import shutil
import subprocess
import sys

import pytest
from conftest import FIXTURES, ROOT

CLI = [sys.executable, str(ROOT / "plugins" / "argus" / "scripts" / "auditor_cli.py")]
FIX = FIXTURES / "rust_runtime"

pytestmark = pytest.mark.skipif(shutil.which("cargo") is None, reason="cargo not installed")


@pytest.fixture(scope="module")
def report(tmp_path_factory):
    out = tmp_path_factory.mktemp("audit")
    work = tmp_path_factory.mktemp("run")
    subprocess.run(["cargo", "build"], cwd=FIX, check=True, capture_output=True, text=True, timeout=300)
    subprocess.run([*CLI, "map", FIX, "--out", out], check=True, capture_output=True, text=True)
    binary = FIX / "target" / "debug" / ("rust_runtime.exe" if sys.platform == "win32" else "rust_runtime")
    subprocess.run([str(binary)], cwd=work, check=True, capture_output=True, text=True, timeout=30)
    traces = sorted(work.glob("trace-*.json"))
    assert traces, "the program did not write a tracing-chrome file"
    r = subprocess.run([*CLI, "trace-import", FIX, "--chrome", traces[0], "--out", out, "--stall-ms", "30"],
                       capture_output=True, text=True)
    assert r.returncode == 0, r.stderr
    return json.loads((out / "trace.json").read_text())


def finding(report, rule, function, line):
    for f in report["findings"]:
        if (f["rule"], f["function"], f["line"]) == (rule, function, line):
            return f["evidence"]
    raise AssertionError(f"no finding {rule} {function}:{line}; have {[(f['rule'], f['function']) for f in report['findings']]}")


def test_real_file_line_keys_are_read(report):
    # a regression check for the bug the real trace found: file/line are top-level `.file`/`.line`
    # keys on the event, not nested under `args`, which the importer originally assumed
    assert report["meta"]["calls"] > 0


def test_sync_http_call_confirmed_as_blocking(report):
    ev = finding(report, "blocking-in-async", "rust_runtime::strategy::tick", 13)
    assert ev["status"] == "confirmed"
    assert "tick" in ev["detail"] and "fetch_price" in ev["detail"]


def test_n_plus_one_confirmed_from_real_counts(report):
    ev = finding(report, "io-in-loop", "rust_runtime::strategy::refresh_all", 26)
    assert ev["status"] == "confirmed" and "3×" in ev["detail"]


def test_spawn_blocking_call_is_not_a_stall(report):
    # fetch_price runs 9x total (6 direct + 3 via spawn_blocking for "ETH"); only the direct,
    # on-runtime calls should count as stalls
    fns = {f["function"]: f for f in report["functions"]}
    assert fns["rust_runtime::exchange::Exchange::fetch_price"]["calls"] == 9
    assert fns["rust_runtime::exchange::Exchange::fetch_price"]["stalls"] == 6
    assert not report["unpredicted_stalls"]
