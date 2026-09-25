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


def _write(repo, name, body):
    d = repo / ".audit" / "repros"
    d.mkdir(parents=True, exist_ok=True)
    (d / name).write_text(body)
    return d / name


def test_files_are_isolated_from_each_other(tmp_path):
    """The failure from a real run: one repro chdir'd and monkeypatched a shared module without undoing it,
    so the next file's relative path and its HTTP client pointed at a dead server."""
    (tmp_path / "app").mkdir()
    (tmp_path / "app" / "main.py").write_text("X = 1\n")
    _write(tmp_path, "test_a_polluter.py",
           "import os, tempfile, json\n"
           "def test_pollute():\n"
           "    os.chdir(tempfile.mkdtemp())\n"
           "    json.dumps = lambda *a, **k: 'patched'\n")
    _write(tmp_path, "test_b_victim.py",
           "import json\n"
           "from auditor.repro.harness import evidence, repo_path\n"
           "def test_relative_and_module_state():\n"
           "    assert open('app/main.py').read() == 'X = 1\\n'\n"
           "    assert repo_path('app', 'main.py').exists()\n"
           "    assert json.dumps(1) == '1'\n"
           "    evidence(finding='x@victim:1')\n")
    res = runner.run(tmp_path, tmp_path / ".audit")
    by = {t["test"]: t for t in res["tests"]}
    assert by["test_relative_and_module_state"]["outcome"] == "passed", by
    assert res["meta"]["isolation"] == "one process per file"


def test_cwd_restored_between_tests_in_one_file(tmp_path):
    (tmp_path / "data.txt").write_text("ok")
    _write(tmp_path, "test_two.py",
           "import os, tempfile\n"
           "def test_1_moves():\n    os.chdir(tempfile.mkdtemp())\n"
           "def test_2_reads():\n    assert open('data.txt').read() == 'ok'\n")
    res = runner.run(tmp_path, tmp_path / ".audit")
    assert {t["test"]: t["outcome"] for t in res["tests"]} == {"test_1_moves": "passed", "test_2_reads": "passed"}


def test_single_file_runs_merge_instead_of_overwriting(tmp_path):
    """Investigators run `repro --file` in parallel: each run keeps everyone else's results."""
    import concurrent.futures as cf
    files = [_write(tmp_path, f"test_p{i}.py",
                    f"from auditor.repro.harness import evidence\ndef test_p{i}():\n    evidence(finding='r@f{i}:1')\n")
             for i in range(4)]
    with cf.ThreadPoolExecutor(4) as ex:
        list(ex.map(lambda f: runner.run(tmp_path, tmp_path / ".audit", f), files))
    data = json.loads((tmp_path / ".audit" / "repro.json").read_text())
    assert sorted(t["finding"] for t in data["tests"]) == [f"r@f{i}:1" for i in range(4)]
    assert runner.status_of(data, "r@f2:1") == "passed"


def test_collection_error_is_reported_per_file(tmp_path):
    _write(tmp_path, "test_broken.py", "import definitely_not_installed_module\ndef test_x():\n    pass\n")
    res = runner.run(tmp_path, tmp_path / ".audit")
    [t] = res["tests"]
    assert t["outcome"] == "error" and t["file"] == "test_broken.py"
    assert runner.status_of(res, "any@x:1", ".audit/repros/test_broken.py") == "failed"


def test_repro_file_outside_this_repo_is_refused(tmp_path):
    """A repro written into a sibling tree must not be run (and recorded) against this repo."""
    other = tmp_path / "other"
    f = _write(other, "test_x.py", "def test_x():\n    pass\n")
    (tmp_path / "repo").mkdir()
    with pytest.raises(ValueError, match="not in"):
        runner.run(tmp_path / "repo", tmp_path / "repo" / ".audit", f)


def test_repo_venv_is_preferred(tmp_path, monkeypatch):
    monkeypatch.delenv("ARGUS_REPRO_PYTHON", raising=False)
    py, why = runner.pick_python(tmp_path, tmp_path / ".audit")
    assert py == sys.executable and "no repo environment" in why
    assert runner.pick_python(tmp_path, tmp_path / ".audit", "/x/python")[0] == "/x/python"
