"""MCP tools call the same code paths as the CLI; exercise them in-process."""
import importlib.util
import json
import shutil
import sys

import pytest
from conftest import FIXTURES, ROOT

pytest.importorskip("mcp.server.mcpserver")
FIX = FIXTURES / "py_runtime"


@pytest.fixture(scope="module")
def srv():
    spec = importlib.util.spec_from_file_location("mcp_server", ROOT / "plugins" / "argus" / "scripts" / "mcp_server.py")
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


def test_tools_registered(srv):
    import asyncio
    names = {t.name for t in asyncio.run(srv.server.list_tools())}
    assert names == {"audit_map", "audit_trace", "audit_trace_import", "audit_lint_import", "run_repro", "audit_queue", "audit_record",
                     "audit_report"}


def test_trace_import_over_mcp(srv, tmp_path):
    repo = tmp_path / "repo"
    repo.mkdir()
    (repo / "m.js").write_text('const { execSync } = require("node:child_process");\n\n'
                               'async function tick() {\n  execSync("x");\n}\n')
    srv.audit_map(str(repo))
    prof = tmp_path / "recorded-elsewhere.json"  # speedscope: 300 ms inside tick, between idle samples
    prof.write_text(json.dumps({
        "shared": {"frames": [{"name": "tick", "file": str(repo / "m.js"), "line": 3}, {"name": "execSync"}]},
        "profiles": [{"type": "sampled", "name": "main", "unit": "milliseconds", "startValue": 0,
                      "samples": [[]] + [[0, 1]] * 30 + [[]], "weights": [10] * 32}]}))
    r = srv.audit_trace_import(str(repo), profiles=[str(prof)])
    assert r["imported"]["profiles"] == 1
    [f] = [x for x in r["findings"] if x["rule"] == "blocking-in-async"]
    assert f["evidence"]["status"] == "confirmed" and "execSync (sampled leaf)" in f["evidence"]["detail"]
    bad = tmp_path / "bad.json"
    bad.write_text("{}")
    assert "expected a V8 .cpuprofile" in srv.audit_trace_import(str(repo), profiles=[str(bad)])["error"]


def test_round_loop_over_mcp(srv, tmp_path):
    repo = tmp_path / "repo"
    shutil.copytree(FIXTURES / "effects" / "python", repo)
    srv.audit_map(str(repo))
    q = srv.audit_queue(str(repo), budget=2, per_round=2)
    assert q["round"] == 1 and len(q["items"]) == 2
    rec = srv.audit_record(str(repo), [{"finding": q["items"][0]["id"], "verdict": "rejected", "reason": "test"}])
    assert rec["recorded"] == [q["items"][0]["id"]]
    assert srv.audit_queue(str(repo))["stop"] == "budget exhausted"
    rep = srv.audit_report(str(repo))
    assert rep["by_status"]["rejected"] == 1 and (repo / ".audit" / "report.html").exists()


@pytest.mark.skipif(sys.version_info < (3, 12), reason="tracer needs sys.monitoring")
def test_map_trace_repro_chain(srv):
    shutil.rmtree(FIX / ".audit", ignore_errors=True)
    m = srv.audit_map(str(FIX))
    assert m["stats"]["findings"] >= 5 and (FIX / ".audit" / "map.json").exists()
    t = srv.audit_trace(str(FIX), [sys.executable, "run.py"])
    assert t["evidence_summary"].get("confirmed", 0) >= 3
    shutil.copytree(FIX / "repros", FIX / ".audit" / "repros")
    r = srv.run_repro(str(FIX))
    assert r["tests"] == [] and r["by_outcome"] == {"passed": r["by_outcome"]["passed"]} and r["by_outcome"]["passed"] > 0
