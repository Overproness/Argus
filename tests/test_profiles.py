"""Function-level runtime evidence from profilers: V8 .cpuprofile, speedscope and V8 coverage."""
import json
import shutil
import subprocess
import sys

import pytest
from conftest import ROOT

from auditor.repro.faults import FaultServer
from auditor.trace import profiles, spans
from auditor.trace.evidence import Evidence
from auditor.trace.store import StallRec, Trace

CLI = [sys.executable, str(ROOT / "plugins" / "argus" / "scripts" / "auditor_cli.py")]
MAP = {
    "functions": [
        {"id": "svc.py:10:0", "qualname": "svc.main", "lang": "python", "lines": [10, 18]},
        {"id": "svc.py:20:0", "qualname": "svc.work", "lang": "python", "lines": [20, 30]},
    ],
    "findings": [
        {"rule": "blocking-in-async", "severity": "high", "lang": "python", "function": "svc.work",
         "file": "svc.py", "line": 22, "chain": [], "message": ""},
    ],
}


def speedscope(repo):
    f = str(repo / "svc.py")
    return {"shared": {"frames": [{"name": "main", "file": f, "line": 10}, {"name": "work", "file": f, "line": 20},
                                  {"name": "sleep"}]},
            "profiles": [
                {"type": "sampled", "name": "MainThread", "unit": "milliseconds", "startValue": 0,
                 "samples": [[0]] * 2 + [[0, 1, 2]] * 15 + [[]] * 3 + [[0]] * 2, "weights": [10] * 22},
                {"type": "evented", "name": "worker", "unit": "seconds", "startValue": 0, "events": [
                    {"type": "O", "frame": 0, "at": 0.0}, {"type": "O", "frame": 1, "at": 0.01},
                    {"type": "C", "frame": 1, "at": 0.2}, {"type": "C", "frame": 0, "at": 0.25}]},
            ]}


def test_speedscope_sampled_and_evented(tmp_path):
    repo = tmp_path / "repo"
    (tmp_path / "trace" / "profiles").mkdir(parents=True)
    (tmp_path / "trace" / "profiles" / "p.speedscope.json").write_text(json.dumps(speedscope(repo)))
    t = profiles.attach(Trace([], [], []), tmp_path / "trace", MAP, repo, 0.1)
    [stall] = t.stalls  # 150 ms inside work, on the main thread
    assert (stall.file, stall.line, stall.qualname) == ("svc.py", 20, "svc.work") and 0.14 < stall.dur < 0.16
    assert [q for _, _, q in stall.stack] == ["svc.main", "svc.work", "sleep (sampled leaf)"]
    evented = {c.qualname: c for c in t.calls if c.pid == max(c.pid for c in t.calls)}
    assert abs(evented["svc.work"].dur - 0.19) < 1e-9 and abs(evented["svc.main"].self_dur - 0.06) < 1e-9
    ev = Evidence(MAP, t).build()
    assert ev["findings"][0]["evidence"]["status"] == "confirmed"


def cpuprofile(samples, start_us=5_000_000):
    """samples: one stack per 1 ms sample, outer->inner, each frame (name, url, 0-based line, 0-based column)."""
    nodes, ids = [{"id": 1, "callFrame": {"functionName": "(root)", "url": "", "lineNumber": -1,
                                          "columnNumber": -1}, "children": []}], {}

    def node_for(path):
        if path in ids:
            return ids[path]
        parent = node_for(path[:-1]) if len(path) > 1 else 1
        name, u, line, col = path[-1]
        nid = len(nodes) + 1
        nodes.append({"id": nid, "callFrame": {"functionName": name, "url": u, "lineNumber": line,
                                               "columnNumber": col}, "children": []})
        next(n for n in nodes if n["id"] == parent)["children"].append(nid)
        ids[path] = nid
        return nid

    sids = [node_for(tuple(st)) for st in samples]
    return {"nodes": nodes, "startTime": start_us, "endTime": start_us + 1000 * len(sids), "samples": sids,
            "timeDeltas": [1000] * len(sids)}


def test_cpuprofile_clock_startup_and_heartbeat_merge(tmp_path):
    repo = tmp_path / "repo"
    url = (repo / "m.js").as_uri()
    wrapper, cb = ("", url, 0, 0), ("", url, 2, 11)  # module top level; a timer callback at line 3
    crunch, idle, lib = ("crunch", url, 0, 0), ("(idle)", "", -1, -1), ("parse", "node:internal/x", 5, 0)
    prof = tmp_path / "trace" / "node-prof" / "1-1"
    prof.mkdir(parents=True)
    # 200 ms of module loading before the loop first idles, then crunch blocks the loop for 150 ms.
    doc = cpuprofile([[wrapper]] * 200 + [[idle]] * 3 + [[cb, crunch]] * 150 + [[idle]] * 2)
    (prof / "CPU.20260919.174720.4242.0.001.cpuprofile").write_text(json.dumps(doc))
    # Another process (npm, say) busy for 300 ms without ever running repo code.
    other = cpuprofile([[idle]] * 2 + [[lib]] * 300 + [[idle]] * 2)
    (prof / "CPU.20260919.174720.4243.0.001.cpuprofile").write_text(json.dumps(other))
    epoch0 = 1_700_000_000
    (prof / "_argus_meta.json").write_text(json.dumps({"argv": ["node", "m.js"], "epoch_ns": epoch0 * 10**9,
                                                       "mono_ns": 4_990_000_000}))
    fmap = {"functions": [{"id": "m.js:1:0", "qualname": "m.crunch", "lang": "javascript", "lines": [1, 1]}],
            "findings": []}
    gaps = [StallRec(0, 0, "", 0, "(program-level: no span covered the gap)", 0.14, epoch0 + 0.36, []),
            StallRec(0, 0, "", 0, "(program-level: no span covered the gap)", 0.5, epoch0 + 3.0, [])]
    t = profiles.attach(Trace([], [], gaps), tmp_path / "trace", fmap, repo, 0.1)

    [run] = t.runs  # the other process never ran repo code
    assert run["clock"] == "epoch" and json.loads(run["argv"]) == ["node", "m.js", "(process 4242)"]
    [call] = t.calls  # module loading is not a call of `crunch`, though both start at 1:1
    assert call.qualname == "m.crunch" and abs(call.dur - 0.15) < 1e-6
    by_name = {s.qualname: s for s in t.stalls}
    assert set(by_name) == {"m.crunch", "(program-level: no span covered the gap)"}  # startup is not a stall
    assert abs(by_name["m.crunch"].at - (epoch0 + 0.363)) < 1e-3  # on the epoch clock
    assert by_name["(program-level: no span covered the gap)"].at == epoch0 + 3.0  # the gap crunch explains is gone


def test_cli_trace_import_and_rebuild(tmp_path):
    repo = tmp_path / "repo"
    repo.mkdir()
    (repo / "m.js").write_text("function crunch(ms) {\n  const end = Date.now() + ms;\n  while (Date.now() < end) {}\n}\n")
    subprocess.run(CLI + ["map", str(repo)], check=True, capture_output=True)
    url, idle = (repo / "m.js").as_uri(), ("(idle)", "", -1, -1)
    prof = tmp_path / "saved-from-devtools.cpuprofile"
    prof.write_text(json.dumps(cpuprofile([[idle]] * 2 + [[("", url, 5, 0), ("crunch", url, 0, 17)]] * 150 + [[idle]] * 2)))
    r = subprocess.run(CLI + ["trace-import", str(repo), "--profile", str(prof)], capture_output=True, text=True)
    assert r.returncode == 0, r.stdout + r.stderr
    trace_json = repo / ".audit" / "trace.json"
    [u] = json.loads(trace_json.read_text())["unpredicted_stalls"]
    assert u["function"] == "m.crunch" and abs(u["worst_s"] - 0.15) < 0.01
    # Profiles are re-read on every rebuild: at a 200 ms threshold the 150 ms stretch is no stall.
    r = subprocess.run(CLI + ["trace-report", str(repo), "--stall-ms", "200"], capture_output=True, text=True)
    assert r.returncode == 0, r.stdout + r.stderr
    assert json.loads(trace_json.read_text())["unpredicted_stalls"] == []
    bad = tmp_path / "trace-events.json"
    bad.write_text('{"traceEvents": []}')
    r = subprocess.run(CLI + ["trace-import", str(repo), "--profile", str(bad)], capture_output=True, text=True)
    assert r.returncode == 1 and "expected a V8 .cpuprofile or a speedscope JSON" in r.stderr


def test_dependency_paths_do_not_map_by_suffix(tmp_path):
    m = spans.Mapper({"functions": [{"id": "index.js:1:0", "qualname": "index.main", "lang": "javascript",
                                     "lines": [1, 9]}], "findings": []}, tmp_path)
    assert m.rel(str(tmp_path / "index.js")) == "index.js"
    assert m.rel("/srv/app/index.js") == "index.js"  # another checkout of the repo: matched by suffix
    assert m.rel(str(tmp_path / "node_modules" / "left-pad" / "index.js")) is None
    assert m.rel("/srv/app/node_modules/left-pad/index.js") is None


def test_v8_coverage_counts_map_to_functions(tmp_path):
    repo = tmp_path / "repo"
    repo.mkdir()
    src = "function a() {\n  return [1, 2].map((x) => x);\n}\n\nfunction b() {}\n"
    (repo / "m.js").write_text(src)
    cov = tmp_path / "cov"
    cov.mkdir()
    (cov / "coverage-1.json").write_text(json.dumps({"result": [{"url": (repo / "m.js").as_uri(), "functions": [
        {"functionName": "", "ranges": [{"startOffset": 0, "endOffset": len(src), "count": 1}]},
        {"functionName": "a", "ranges": [{"startOffset": 0, "endOffset": 44, "count": 3}]},
        {"functionName": "", "ranges": [{"startOffset": src.index("(x)"), "endOffset": 40, "count": 6}]},
        {"functionName": "b", "ranges": [{"startOffset": src.index("function b"), "endOffset": len(src), "count": 0}]},
    ]}]}))
    fmap = {"functions": [{"id": "m.js:1:0", "qualname": "m.a", "lang": "javascript", "lines": [1, 3]},
                          {"id": "m.js:5:0", "qualname": "m.b", "lang": "javascript", "lines": [5, 5]}],
            "findings": []}
    counts = {c.qualname: c.count for c in profiles.coverage_counts([cov / "coverage-1.json"], spans.Mapper(fmap, repo))}
    assert counts == {"m.a": 3, "m.b": 0}  # the module wrapper and the inner arrow are not map functions


NODE_APP = """const { execSync } = require("node:child_process");
const http = require("node:http");

function crunch(ms) {
  const end = Date.now() + ms;
  while (Date.now() < end) {}
}

function get(url) {
  return new Promise((resolve, reject) => {
    http.get(url, (res) => { res.resume(); res.on("end", resolve); }).on("error", reject);
  });
}

async function tick() {
  execSync('node -e "setTimeout(()=>{},400)"');
}

async function main(url) {
  await tick();
  for (let i = 0; i < 3; i++) {
    await get(url);
  }
  crunch(300);
}

main(process.argv[2]);
"""


@pytest.mark.skipif(shutil.which("node") is None, reason="node not installed")
def test_node_profile_end_to_end(tmp_path):
    repo = tmp_path / "app"
    repo.mkdir()
    (repo / "app.cjs").write_text(NODE_APP)
    subprocess.run(CLI + ["map", str(repo)], check=True, capture_output=True)
    srv = FaultServer(body="ok").start()
    try:
        r = subprocess.run(CLI + ["trace", str(repo), "--", "node", "app.cjs", srv.url], capture_output=True,
                           text=True, timeout=180)
    finally:
        srv.stop()
    assert r.returncode == 0, r.stdout + r.stderr
    assert "node: sampling profile" in r.stdout  # switched on because the command is `node`
    data = json.loads((repo / ".audit" / "trace.json").read_text())
    ev = {(f["rule"], f["function"]): f["evidence"] for f in data["findings"]}
    block = ev[("blocking-in-async", "app.tick")]
    assert block["status"] == "confirmed" and "app.main → app.tick" in block["detail"]
    loop = ev[("io-in-loop", "app.main")]
    assert loop["status"] == "confirmed" and "`app.get` ran 3× while `app.main` ran 1×" in loop["detail"]
    [u] = data["unpredicted_stalls"]
    assert u["function"] == "app.crunch" and u["worst_s"] >= 0.2
    assert all(run["source"] == "v8-cpuprofile" for run in data["meta"]["runs"])
    assert data["meta"]["runs"][0]["argv"][:2] == ["node", "app.cjs"]


@pytest.mark.skipif(shutil.which("npm") is None, reason="npm not installed")
def test_npm_script_is_profiled(tmp_path):
    repo = tmp_path / "app"
    repo.mkdir()
    (repo / "package.json").write_text(json.dumps({"name": "app", "version": "1.0.0", "private": True,
                                                   "scripts": {"start": "node app.cjs"}}))
    (repo / "app.cjs").write_text("function crunch(ms) {\n  const end = Date.now() + ms;\n  while (Date.now() < end) {}\n}\n\n"
                                  "async function main() {\n  crunch(300);\n}\n\nmain();\n")
    subprocess.run(CLI + ["map", str(repo)], check=True, capture_output=True)
    r = subprocess.run(CLI + ["trace", str(repo), "--", "npm", "start"], capture_output=True, text=True, timeout=180)
    assert r.returncode == 0, r.stdout + r.stderr
    data = json.loads((repo / ".audit" / "trace.json").read_text())
    [u] = data["unpredicted_stalls"]  # from the node child; npm's own process ran no repo code and is dropped
    assert u["function"] == "app.crunch" and u["worst_s"] >= 0.2
    assert all(run["argv"][:2] == ["npm", "start"] for run in data["meta"]["runs"])


@pytest.mark.skipif(shutil.which("node") is None, reason="node not installed")
def test_node_heartbeat_gap_is_named_by_the_profile(tmp_path):
    repo = tmp_path / "app"
    repo.mkdir()
    (repo / "loop.mjs").write_text(
        "function crunch(ms) { const end = Date.now() + ms; while (Date.now() < end) {} }\n"
        "const t = setInterval(() => console.log('tick'), 20);\n"
        "setTimeout(() => crunch(600), 150);\n"
        "setTimeout(() => clearInterval(t), 1200);\n")
    subprocess.run(CLI + ["map", str(repo)], check=True, capture_output=True)
    r = subprocess.run(CLI + ["trace", str(repo), "--heartbeat", "^tick", "--", "node", "loop.mjs"],
                       capture_output=True, text=True, timeout=120)
    assert r.returncode == 0, r.stdout + r.stderr
    data = json.loads((repo / ".audit" / "trace.json").read_text())
    # Both channels saw the 600 ms stall; the heartbeat alone could only call it program-level.
    [u] = data["unpredicted_stalls"]
    assert u["function"] == "loop.crunch" and u["stalls"] == 1 and 0.4 < u["worst_s"] < 1.0
    assert {run["source"] for run in data["meta"]["runs"]} == {"heartbeat", "v8-cpuprofile"}
