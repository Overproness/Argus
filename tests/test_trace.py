"""End to end: map the runtime fixture, run it under the tracer, check the evidence."""
import json
import shutil
import subprocess
import sys

import pytest
from conftest import FIXTURES, ROOT

CLI = ROOT / "plugins" / "argus" / "scripts" / "auditor_cli.py"
FIX = FIXTURES / "py_runtime"

pytestmark = pytest.mark.skipif(sys.version_info < (3, 12), reason="tracer needs sys.monitoring")


@pytest.fixture(scope="module")
def report():
    shutil.rmtree(FIX / ".audit", ignore_errors=True)
    subprocess.run([sys.executable, CLI, "map", FIX], check=True, capture_output=True)
    subprocess.run([sys.executable, CLI, "trace", FIX, "--", sys.executable, "run.py"], check=True,
                   capture_output=True, timeout=120)
    return json.loads((FIX / ".audit" / "trace.json").read_text())


def evidence(report, rule, function, line):
    for f in report["findings"]:
        if (f["rule"], f["function"], f["line"]) == (rule, function, line):
            return f["evidence"]
    raise AssertionError(f"no finding {rule} {function}:{line}")


def test_blocking_chain_confirmed(report):
    ev = evidence(report, "blocking-in-async", "app.service.tick", 30)
    assert ev["status"] == "confirmed"
    assert "tick → compute → fetch_quote" in ev["detail"]
    assert ev["stats"]["stalls"] >= 1  # the stall lands on tick's leaf... via fetch_quote stack


def test_direct_sleep_confirmed(report):
    ev = evidence(report, "blocking-in-async", "app.service.tick", 32)
    assert ev["status"] == "confirmed"
    assert ev["detail"].endswith("main → tick")


def test_offloaded_calls_are_not_stalls(report):
    fns = {f["function"]: f for f in report["functions"]}
    assert fns["app.service.fetch_quote_bounded"]["calls"] == 2
    assert fns["app.service.fetch_quote_bounded"]["stalls"] == 0
    assert not report["unpredicted_stalls"]


def test_n_plus_one_counted(report):
    ev = evidence(report, "io-in-loop", "app.service.tick", 30)
    assert ev["status"] == "confirmed" and "up to 2×" in ev["detail"]


def test_timeout_not_verifiable_but_timed(report):
    ev = evidence(report, "io-without-timeout", "app.service.fetch_quote", 9)
    assert ev["status"] == "not-verifiable"
    assert ev["stats"]["calls"] == 7


def test_complexity_and_recursion_measured(report):
    ev = evidence(report, "nested-loops", "app.service.matrix", 20)
    assert ev["status"] == "measured" and 1.3 <= ev["fit"]["exponent"] <= 2.5
    ev = evidence(report, "recursion", "app.service.depth", 24)
    assert ev["status"] == "measured" and ev["depth"] == 26


def test_class_bodies_not_recorded(report):
    assert all(f["function"] != "Slow" for f in report["functions"])
