"""Language-neutral reproduction: the fault server, run_target, black-box runs, native probes per language.

Every native-probe test first builds and runs the untouched scaffold (so the templates themselves are
verified), then a probe that reproduces a finding against the fault server or with scaling inputs.
"""
import http.client
import shutil
import socket
import subprocess
import sys
import time
import urllib.error
import urllib.request

import pytest
from conftest import FIXTURES

from auditor.repro import native
from auditor.repro.faults import FaultServer, fault_server
from auditor.trace.fit import fit_power

NATIVE = FIXTURES / "native"
NET_ERRORS = (ConnectionError, http.client.HTTPException, urllib.error.URLError, OSError)


def has(tool):
    return shutil.which(tool) is not None


def copy(tmp_path, name):
    dst = tmp_path / name
    shutil.copytree(NATIVE / name, dst)
    return dst


def kinds(res, kind):
    return [e for e in res["evidence"] if e.get("kind") == kind]


def alive(pid):
    if sys.platform == "win32":
        out = subprocess.run(["tasklist", "/FI", f"PID eq {pid}", "/NH"], capture_output=True, text=True).stdout
        return str(pid) in out
    try:
        import os
        os.kill(pid, 0)
        return True
    except OSError:
        return False


# --- fault server -------------------------------------------------------------------------------

def test_latency_body_and_counts():
    with fault_server(latency=0.3, body='{"p": 1}') as srv:
        t0 = time.perf_counter()
        body = urllib.request.urlopen(srv.url, timeout=5).read()
        elapsed = time.perf_counter() - t0
    assert body == b'{"p": 1}' and elapsed >= 0.3
    s = srv.summary()
    assert s["connections"] == 1 and s["actions"] == {"respond": 1} and s["requests"][0].startswith("GET / ")


def test_hang_holds_the_caller():
    with fault_server(hang=True) as srv, pytest.raises(NET_ERRORS):
        urllib.request.urlopen(srv.url, timeout=0.5)
    assert srv.summary()["actions"] == {"hang": 1}


def test_fail_first_then_recover():
    with fault_server(fail_first=2) as srv:
        results = []
        for _ in range(3):
            try:
                urllib.request.urlopen(srv.url, timeout=2).read()
                results.append("ok")
            except NET_ERRORS:
                results.append("err")
    assert results == ["err", "err", "ok"]
    assert srv.summary()["actions"] == {"reset": 2, "respond": 1}


def test_proxy_mode_and_faults_changed_mid_run():
    upstream = FaultServer(body="upstream").start()
    try:
        with fault_server(upstream=(upstream.host, upstream.port)) as proxy:
            assert urllib.request.urlopen(f"http://{proxy.address}", timeout=2).read() == b"upstream"
            proxy.set(latency=0.3)
            t0 = time.perf_counter()
            urllib.request.urlopen(f"http://{proxy.address}", timeout=5).read()
            assert time.perf_counter() - t0 >= 0.3
        assert proxy.summary()["actions"] == {"proxy": 2}
    finally:
        upstream.stop()


# --- observing any program -----------------------------------------------------------------------

def test_commands_resolve_like_a_shell():
    from auditor.procs import resolve
    assert resolve(["./local-tool", 1]) == ["./local-tool", "1"]  # paths are left alone
    if sys.platform == "win32" and has("npm"):
        assert resolve(["npm", "--version"])[0].lower().endswith(".cmd")  # the shim CreateProcess cannot find
    if has("npm"):
        assert native.run_target(["npm", "--version"], timeout=60, emit=False)["exit_code"] == 0


def test_run_target_heartbeat_gap_and_evidence(tmp_path):
    script = tmp_path / "tick.py"
    script.write_text(
        "import time\n"
        "for _ in range(5):\n    print('tick', flush=True); time.sleep(0.05)\n"
        "time.sleep(0.4)\nprint('tick', flush=True)\n"
        "print('@@evidence {\"kind\": \"custom\", \"n\": 7}', flush=True)\n")
    res = native.run_target([sys.executable, str(script)], heartbeat=r"^tick", timeout=20)
    assert res["returned"] and res["exit_code"] == 0 and res["heartbeats"] == 6
    assert 0.35 <= res["max_heartbeat_gap_s"] < 2
    assert {"kind": "custom", "n": 7} in res["evidence"]


def test_run_target_kills_the_whole_tree(tmp_path):
    script = tmp_path / "spawner.py"
    script.write_text(
        "import subprocess, sys, time\n"
        "child = subprocess.Popen([sys.executable, '-c', 'import time; time.sleep(60)'])\n"
        "print(child.pid, flush=True)\ntime.sleep(60)\n")
    res = native.run_target([sys.executable, str(script)], timeout=2)
    assert res["returned"] is False and res["exit_code"] is None
    child = int(res["tail"][0])
    time.sleep(0.5)
    assert not alive(child)


def test_black_box_program_against_a_dead_peer(tmp_path):
    # Any program in any language: here a small Python client with no timeout, pointed at the fault server.
    prog = tmp_path / "client.py"
    prog.write_text("import os, urllib.request\nurllib.request.urlopen(os.environ['DEP_URL']).read()\nprint('done')\n")
    with fault_server(hang=True) as srv:
        res = native.run_target([sys.executable, str(prog)], env={"DEP_URL": srv.url}, timeout=2)
    assert res["returned"] is False and srv.summary()["connections"] == 1


# --- native probes, one per installed toolchain ------------------------------------------------------

RUST_PROBE = r'''
fn main() {
    let url = std::env::var("ARGUS_FAULT_URL").unwrap();
    let mode = std::env::var("MODE").unwrap();
    println!("@@evidence {{\"kind\": \"calling\", \"mode\": \"{}\"}}", mode);
    let t0 = std::time::Instant::now();
    let ok = if mode == "retry" {
        argus_fixture_client::fetch_with_retries(&url, 3).is_ok()
    } else {
        argus_fixture_client::fetch(&url).is_ok()
    };
    println!("@@evidence {{\"kind\": \"result\", \"ok\": {}, \"elapsed_s\": {:.4}}}", ok, t0.elapsed().as_secs_f64());
}
'''


@pytest.mark.skipif(not has("cargo"), reason="cargo not installed")
def test_rust_probe_hang_and_retry_burst(tmp_path):
    repo = copy(tmp_path, "rust_lib")
    probe = native.scaffold("rust", "client", repo=repo)
    assert probe.verified and probe.dir == repo / ".audit" / "repros" / "native" / "rust" / "client"
    native.build_probe(probe)
    assert kinds(native.run_probe(probe), "probe")  # the untouched template runs
    probe.source.write_text(RUST_PROBE)
    native.build_probe(probe)
    with fault_server(hang=True) as srv:
        hung = native.run_probe(probe, env={"ARGUS_FAULT_URL": srv.url, "MODE": "once"}, timeout=3)
    assert hung["returned"] is False and kinds(hung, "calling") and not kinds(hung, "result")
    with fault_server(reset=True) as srv:
        burst = native.run_probe(probe, env={"ARGUS_FAULT_URL": srv.url, "MODE": "retry"}, timeout=20)
        s = srv.summary()
    assert burst["returned"] and kinds(burst, "result")[0]["ok"] is False
    assert s["connections"] == 3 and s["max_gap_s"] < 0.5  # three attempts, no backoff


@pytest.mark.skipif(not has("node"), reason="node not installed")
def test_javascript_probe_event_loop_stall_and_hang(tmp_path):
    repo = copy(tmp_path, "js_lib")
    probe = native.scaffold("javascript", "client", repo=repo, module="client.mjs")
    assert kinds(native.run_probe(probe), "probe")
    mod = (repo / "client.mjs").as_uri()
    probe.source.write_text(
        f'const mod = await import("{mod}");\n'
        "const t = setInterval(() => console.log('tick'), 20);\n"
        "setTimeout(() => mod.crunch(400), 100);\n"
        "setTimeout(() => clearInterval(t), 800);\n")
    stall = native.run_probe(probe, heartbeat=r"^tick", timeout=20)
    assert stall["returned"] and stall["max_heartbeat_gap_s"] >= 0.35  # the loop froze for crunch(400)
    probe.source.write_text(
        f'const mod = await import("{mod}");\n'
        'console.log("@@evidence " + JSON.stringify({kind: "calling"}));\n'
        "await mod.fetchPrice(process.env.ARGUS_FAULT_URL);\n"
        'console.log("@@evidence " + JSON.stringify({kind: "result"}));\n')
    with fault_server(hang=True) as srv:
        hung = native.run_probe(probe, env={"ARGUS_FAULT_URL": srv.url}, timeout=3)
    assert hung["returned"] is False and kinds(hung, "calling") and not kinds(hung, "result")


@pytest.mark.skipif(not (has("node") and has("tsx")), reason="node/tsx not installed")
def test_typescript_probe_event_loop_stall_and_hang(tmp_path):
    repo = copy(tmp_path, "ts_lib")
    probe = native.scaffold("typescript", "client", repo=repo, module="client.ts")
    assert kinds(native.run_probe(probe), "probe")
    mod = (repo / "client.ts").as_uri()
    probe.source.write_text(
        f'const mod = await import("{mod}");\n'
        "const t = setInterval(() => console.log('tick'), 20);\n"
        "setTimeout(() => mod.crunch(400), 100);\n"
        "setTimeout(() => clearInterval(t), 800);\n")
    stall = native.run_probe(probe, heartbeat=r"^tick", timeout=20)
    assert stall["returned"] and stall["max_heartbeat_gap_s"] >= 0.35  # the loop froze for crunch(400)
    probe.source.write_text(
        f'const mod = await import("{mod}");\n'
        'console.log("@@evidence " + JSON.stringify({kind: "calling"}));\n'
        "await mod.fetchPrice(process.env.ARGUS_FAULT_URL);\n"
        'console.log("@@evidence " + JSON.stringify({kind: "result"}));\n')
    with fault_server(hang=True) as srv:
        hung = native.run_probe(probe, env={"ARGUS_FAULT_URL": srv.url}, timeout=3)
    assert hung["returned"] is False and kinds(hung, "calling") and not kinds(hung, "result")


JAVA_PROBE = r'''
public class Probe {
    public static void main(String[] args) throws Exception {
        System.out.println("@@evidence {\"kind\": \"calling\"}");
        demo.Client.fetch(System.getenv("ARGUS_FAULT_URL"));
        System.out.println("@@evidence {\"kind\": \"result\"}");
    }
}
'''


@pytest.mark.skipif(not (has("javac") and has("java")), reason="JDK not installed")
def test_java_probe_hang(tmp_path):
    repo = copy(tmp_path, "java_lib")
    probe = native.scaffold("java", "client", repo=repo)
    native.build_probe(probe)  # no build output in the repo: compiles src/main/java
    assert kinds(native.run_probe(probe, timeout=60), "probe")
    probe.source.write_text(JAVA_PROBE)
    with fault_server(hang=True) as srv:
        hung = native.run_probe(probe, env={"ARGUS_FAULT_URL": srv.url}, timeout=8)
    assert hung["returned"] is False and kinds(hung, "calling") and not kinds(hung, "result")


GO_PROBE = r'''
package main

import (
	"encoding/json"
	"fmt"
	"os"

	fixture "argus/fixture/goclient"
)

func evidence(fact map[string]any) {
	b, _ := json.Marshal(fact)
	fmt.Println("@@evidence " + string(b))
}

func main() {
	mode := os.Getenv("MODE")
	url := os.Getenv("ARGUS_FAULT_URL")
	evidence(map[string]any{"kind": "calling"})
	var err error
	if mode == "retry" {
		_, err = fixture.FetchWithRetries(url, 3)
	} else {
		_, err = fixture.Fetch(url)
	}
	evidence(map[string]any{"kind": "result", "ok": err == nil})
}
'''


@pytest.mark.skipif(not has("go"), reason="go not installed")
def test_go_probe_hang_and_retry_burst(tmp_path):
    repo = copy(tmp_path, "go_lib")
    probe = native.scaffold("go", "client", repo=repo)
    native.build_probe(probe)
    assert kinds(native.run_probe(probe, timeout=60), "probe")  # the untouched template runs
    probe.source.write_text(GO_PROBE)
    native.build_probe(probe)
    with fault_server(hang=True) as srv:
        hung = native.run_probe(probe, env={"ARGUS_FAULT_URL": srv.url, "MODE": "once"}, timeout=8)
    assert hung["returned"] is False and kinds(hung, "calling") and not kinds(hung, "result")
    with fault_server(reset=True) as srv:
        burst = native.run_probe(probe, env={"ARGUS_FAULT_URL": srv.url, "MODE": "retry"}, timeout=20)
        s = srv.summary()
    assert burst["returned"] and kinds(burst, "result")[0]["ok"] is False
    assert s["connections"] == 3 and s["max_gap_s"] < 0.5  # three attempts, no backoff


C_PROBE = r'''
#include <stdio.h>
#include <stdlib.h>
#include <time.h>
#include "pairs.h"

static double now_s(void) { struct timespec ts; timespec_get(&ts, TIME_UTC); return ts.tv_sec + ts.tv_nsec / 1e9; }

int main(void) {
    int sizes[] = {1000, 2000, 4000, 8000, 16000};
    for (int s = 0; s < 5; s++) {
        int n = sizes[s];
        int *xs = malloc(sizeof(int) * n);
        for (int i = 0; i < n; i++) xs[i] = i % 97;
        double best = 1e9; long pairs = 0;
        for (int r = 0; r < 3; r++) {
            double t0 = now_s();
            pairs = count_equal_pairs(xs, n);
            double dt = now_s() - t0;
            if (dt < best) best = dt;
        }
        printf("@@evidence {\"kind\": \"timing\", \"n\": %d, \"seconds\": %.6f, \"pairs\": %ld}\n", n, best, pairs);
        free(xs);
    }
    return 0;
}
'''

CPP_PROBE = r'''
#include <chrono>
#include <cstdio>
#include "dedupe.hpp"

int main() {
    for (int n : {1000, 2000, 4000, 8000, 16000}) {
        std::vector<int> xs(n);
        for (int i = 0; i < n; i++) xs[i] = i;
        double best = 1e9; size_t kept = 0;
        for (int r = 0; r < 3; r++) {
            auto t0 = std::chrono::steady_clock::now();
            kept = dedupe(xs).size();
            double dt = std::chrono::duration<double>(std::chrono::steady_clock::now() - t0).count();
            if (dt < best) best = dt;
        }
        std::printf("@@evidence {\"kind\": \"timing\", \"n\": %d, \"seconds\": %.6f, \"kept\": %zu}\n", n, best, kept);
    }
}
'''


def _scaling(res):
    pts = [(e["n"], e["seconds"]) for e in kinds(res, "timing")]
    return fit_power(pts)


@pytest.mark.skipif(not has("gcc"), reason="gcc not installed")
def test_c_probe_quadratic_scaling(tmp_path):
    repo = copy(tmp_path, "c_lib")
    probe = native.scaffold("c", "pairs", repo=repo, sources=["pairs.c"])
    native.build_probe(probe)
    assert kinds(native.run_probe(probe), "probe")
    probe.source.write_text(C_PROBE)
    native.build_probe(probe)
    fit = _scaling(native.run_probe(probe, timeout=120))
    # quadratic is 2.0; 1.4 still cleanly rejects linear (~1.0) and n log n (~1.1) while tolerating timer
    # noise at sub-millisecond sizes, which under a loaded run once fitted a real quadratic at 1.59
    assert fit is not None and 1.4 <= fit.exponent <= 2.6, fit


@pytest.mark.skipif(not has("g++"), reason="g++ not installed")
def test_cpp_probe_quadratic_scaling(tmp_path):
    repo = copy(tmp_path, "cpp_lib")
    probe = native.scaffold("cpp", "dedupe", repo=repo)
    native.build_probe(probe)
    assert kinds(native.run_probe(probe), "probe")
    probe.source.write_text(CPP_PROBE)
    native.build_probe(probe)
    fit = _scaling(native.run_probe(probe, timeout=120))
    # quadratic is 2.0; 1.4 still cleanly rejects linear (~1.0) and n log n (~1.1) while tolerating timer
    # noise at sub-millisecond sizes, which under a loaded run once fitted a real quadratic at 1.59
    assert fit is not None and 1.4 <= fit.exponent <= 2.6, fit


def test_go_module_replace_directive_renders_without_the_toolchain(tmp_path):
    """The go.mod templating (module name, replace directive) needs no `go` binary to check."""
    (tmp_path / "go.mod").write_text("module example.com/svc\n\ngo 1.22\n")
    p = native.scaffold("go", "x", repo=tmp_path)
    assert p.source.exists() and p.run and p.verified
    assert "@@evidence" in p.source.read_text(encoding="utf8")
    assert "replace example.com/svc =>" in (tmp_path / ".audit/repros/native/go/x/go.mod").read_text()
    with pytest.raises(ValueError, match="black-box"):
        native.scaffold("cobol", "x", repo=tmp_path)
