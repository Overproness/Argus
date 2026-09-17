import shutil

import pytest
from conftest import FIXTURES

from auditor import scip
from auditor.analysis import RepoMap


def _edges(m):
    return sorted((m.functions[e.caller].qualname, m.functions[e.callee].qualname, e.confidence) for e in m.edges)


def test_name_based_resolution_is_ambiguous():
    m = RepoMap(FIXTURES / "scip_rust").load()
    run_targets = {c for a, c, _ in _edges(m) if a.endswith("::run")}
    assert run_targets == {"scip_rust::Live::quote", "scip_rust::Cached::quote"}


@pytest.mark.skipif(shutil.which("rust-analyzer") is None, reason="rust-analyzer not installed")
def test_scip_resolution_is_precise(tmp_path):
    log = scip.run_indexers(FIXTURES / "scip_rust", {"rust-analyzer"}, tmp_path, timeout=600)
    assert any(line.startswith("ok") for line in log), log
    precise = scip.load(tmp_path)
    assert "src/main.rs" in precise.files
    m = RepoMap(FIXTURES / "scip_rust", precise=precise).load()
    edges = _edges(m)
    assert ("scip_rust::run", "scip_rust::Live::quote", "scip") in edges
    assert not any(a == "scip_rust::run" and c.endswith("Cached::quote") for a, c, _ in edges)
    assert ("scip_rust::main", "scip_rust::Cached::quote", "scip") in edges
    assert ("scip_rust::main", "scip_rust::run", "scip") in edges


def test_varint_decoder():
    buf = memoryview(bytes([0xAC, 0x02, 0x05]))
    assert scip._packed(buf) == [300, 5]
