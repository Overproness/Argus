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
    spec = importlib.util.spec_from_file_location("mcp_server", ROOT / "plugins" / "repo-auditor" / "scripts" / "mcp_server.py")
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


def test_tools_registered(srv):
    import asyncio
    names = {t.name for t in asyncio.run(srv.server.list_tools())}
    assert names == {"audit_map", "audit_trace", "run_repro"}


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
