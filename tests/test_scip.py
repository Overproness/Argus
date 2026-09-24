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


@pytest.mark.skipif(shutil.which("scip-python") is None, reason="scip-python not installed")
def test_scip_python_resolution_is_precise(tmp_path):
    log = scip.run_indexers(FIXTURES / "scip_python", {"scip-python"}, tmp_path, timeout=600)
    assert any(line.startswith("ok") for line in log), log
    precise = scip.load(tmp_path)
    assert "main.py" in precise.files
    m = RepoMap(FIXTURES / "scip_python", precise=precise).load()
    edges = _edges(m)
    assert ("run", "Live.quote", "scip") in edges
    assert not any(a == "run" and c == "Cached.quote" for a, c, _ in edges)
    # without SCIP, name-based resolution cannot tell `run`'s `Live` from `Cached`
    m2 = RepoMap(FIXTURES / "scip_python").load()
    assert {c for a, c, _ in _edges(m2) if a == "run"} == {"Live.quote", "Cached.quote"}


@pytest.mark.skipif(shutil.which("scip-typescript") is None, reason="scip-typescript not installed")
def test_scip_typescript_resolution_is_precise(tmp_path):
    log = scip.run_indexers(FIXTURES / "scip_typescript", {"scip-typescript"}, tmp_path, timeout=600)
    assert any(line.startswith("ok") for line in log), log
    precise = scip.load(tmp_path)
    assert "src/main.ts" in precise.files
    m = RepoMap(FIXTURES / "scip_typescript", precise=precise).load()
    edges = _edges(m)
    assert ("run", "Live.quote", "scip") in edges
    assert not any(a == "run" and c == "Cached.quote" for a, c, _ in edges)
    m2 = RepoMap(FIXTURES / "scip_typescript").load()
    assert {c for a, c, _ in _edges(m2) if a == "run"} == {"Live.quote", "Cached.quote"}


@pytest.mark.skipif(shutil.which("scip-go") is None, reason="scip-go not installed")
def test_scip_go_resolution_is_precise(tmp_path):
    log = scip.run_indexers(FIXTURES / "scip_go", {"scip-go"}, tmp_path, timeout=600)
    assert any(line.startswith("ok") for line in log), log
    precise = scip.load(tmp_path)
    assert "main.go" in precise.files
    m = RepoMap(FIXTURES / "scip_go", precise=precise).load()
    edges = _edges(m)
    assert ("run", "Live.Quote", "scip") in edges
    assert not any(a == "run" and c == "Cached.Quote" for a, c, _ in edges)
    m2 = RepoMap(FIXTURES / "scip_go").load()
    assert {c for a, c, _ in _edges(m2) if a == "run"} == {"Live.Quote", "Cached.Quote"}


def test_varint_decoder():
    buf = memoryview(bytes([0xAC, 0x02, 0x05]))
    assert scip._packed(buf) == [300, 5]
