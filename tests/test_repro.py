"""Reproduction harness and runner, end to end on the runtime fixture."""
import asyncio
import json
import shutil
import subprocess
import sys
import threading
import time
import urllib.request
from http.server import BaseHTTPRequestHandler, HTTPServer

import pytest
from conftest import FIXTURES, ROOT

from auditor.repro import harness, runner

CLI = ROOT / "plugins" / "argus" / "scripts" / "auditor_cli.py"
FIX = FIXTURES / "py_runtime"


class Quick(BaseHTTPRequestHandler):
    def do_GET(self):
        self.send_response(200)
        self.end_headers()
        self.wfile.write(b"1")

    def log_message(self, *a):
        pass


@pytest.fixture(scope="module")
def server():
    srv = HTTPServer(("127.0.0.1", 0), Quick)
    threading.Thread(target=srv.serve_forever, daemon=True).start()
    yield f"http://127.0.0.1:{srv.server_port}"
    srv.shutdown()


def test_latency_delays_connect(server, capsys):
    t0 = time.perf_counter()
    with harness.latency(0.2):
        urllib.request.urlopen(server).read()
    assert time.perf_counter() - t0 >= 0.2
    t0 = time.perf_counter()
    urllib.request.urlopen(server).read()
    assert time.perf_counter() - t0 < 0.2  # patch removed


def test_refuse_remote_blocks_non_loopback():
    with harness.refuse_remote(), pytest.raises(urllib.error.URLError, match="only loopback"):
        urllib.request.urlopen("http://10.255.255.1:9/", timeout=1)


def test_loop_monitor_sees_blocking(capsys):
    async def scenario():
        async with harness.loop_monitor(0.01) as m:
            time.sleep(0.15)
            await asyncio.sleep(0.02)
        return m.max_lag

    assert asyncio.run(scenario()) >= 0.1
    assert '"kind": "loop_lag"' in capsys.readouterr().out


def test_call_with_deadline_gives_up(server):
    with harness.hang():
        res = harness.call_with_deadline(urllib.request.urlopen, 0.3, server)
    assert res["returned"] is False and res["elapsed_s"] < 1


def test_scaling_fits_quadratic():
    fit = harness.scaling(lambda rows: [[a * b for b in rows] for a in rows], [50, 100, 200, 400, 800, 1600],
                          lambda n: list(range(n)), repeat=5)
    assert fit is not None and 1.6 <= fit.exponent <= 2.8, fit


def test_outage_retries_are_counted(capsys):
    def fetch_with_retries():
        for _ in range(3):
            try:
                return urllib.request.urlopen("http://127.0.0.1:9/", timeout=1).read()
            except OSError:
                continue

    with harness.fail_connect(), harness.count_connects() as box:
        assert fetch_with_retries() is None
    assert box["connects"] == 3
    assert max(box["gaps_s"]) < 0.5  # no backoff between attempts
    assert '"kind": "connects", "connects": 3' in capsys.readouterr().out


def test_count_calls():
    import math
    with harness.count_calls(math, "sqrt") as box:
        for i in range(5):
            math.sqrt(i)
    assert box["calls"] == 5


@pytest.mark.skipif(sys.version_info < (3, 12), reason="fixture relies on 3.12 asyncio timings")
def test_runner_collects_verdicts():
    repros = FIX / ".audit" / "repros"
    shutil.rmtree(repros, ignore_errors=True)
    shutil.copytree(FIX / "repros", repros)
    r = subprocess.run([sys.executable, CLI, "repro", FIX], capture_output=True, text=True, timeout=180)
    assert r.returncode == 0, r.stdout + r.stderr
    data = json.loads((FIX / ".audit" / "repro.json").read_text())
    by = {t["test"]: t for t in data["tests"]}
    assert by["test_slow_peer_freezes_loop"]["outcome"] == "passed"
    assert by["test_slow_peer_freezes_loop"]["finding"] == "blocking-in-async@app.service.tick:30"
    assert any(e.get("kind") == "loop_lag" and e["max_lag_s"] >= 0.25
               for e in by["test_slow_peer_freezes_loop"]["evidence"])
    assert by["test_matrix_is_quadratic"]["outcome"] == "passed"
    assert by["test_matrix_is_quadratic"]["file"] == "test_matrix_scaling.py"
    md = (FIX / ".audit" / "repro.md").read_text()
    assert "2 passed" in md and "blocking-in-async@app.service.tick:30" in md


def test_runner_reports_failed_repro(tmp_path):
    (tmp_path / ".audit" / "repros").mkdir(parents=True)
    (tmp_path / ".audit" / "repros" / "test_nope.py").write_text(
        "from auditor.repro.harness import evidence\n"
        "def test_x():\n    evidence(finding='x@y:1', seen=0)\n    assert 0 > 1, 'not reproduced'\n")
    res = runner.run(tmp_path, tmp_path / ".audit")
    assert res["tests"][0]["outcome"] == "failed"
    assert res["tests"][0]["evidence"] == [{"finding": "x@y:1", "seen": 0}]
    assert "not reproduced" in res["tests"][0]["message"]
