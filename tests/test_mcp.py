"""MCP tools call the same code paths as the CLI; exercise them in-process."""
import importlib.util
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
    assert names == {"audit_map", "audit_trace", "run_repro", "audit_queue", "audit_record", "audit_report"}


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
    assert t["summary"].get("confirmed", 0) >= 3
    shutil.copytree(FIX / "repros", FIX / ".audit" / "repros")
    r = srv.run_repro(str(FIX))
    assert {x["outcome"] for x in r["tests"]} == {"passed"}
