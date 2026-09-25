"""One small fixture per language, each with known bugs and known-good lookalikes.

Each case lists findings that must appear as (rule, severity, function, line) and
lines that must stay clean for a rule.
"""
import pytest
from conftest import FIXTURES

from auditor.analysis import RepoMap

CASES = {
    "python": {
        "present": [
            ("blocking-in-async", "high", "app.service.tick", 25),  # via compute -> fetch_quote
            ("blocking-in-async", "high", "app.service.tick", 29),  # time.sleep
            ("io-without-timeout", "medium", "app.service.fetch_quote", 12),
            ("io-in-loop", "medium", "app.service.tick", 25),
            ("io-in-loop", "medium", "app.service.stream", 36),
            ("io-without-timeout", "low", "app.service.stream", 36),  # aiohttp 300s default
            ("lock-across-await", "high", "app.service.tick", 30),
            ("nested-loops", "info", "app.service.matrix", 39),
        ],
        "absent": [("blocking-in-async", 28), ("io-without-timeout", 16)],
        "chain": ("app.service.tick", 25, ["app.service.tick", "app.service.compute", "app.service.fetch_quote"]),
    },
    "typescript": {
        "present": [
            # sync fs on the event loop: medium (short syscalls), not high
            ("blocking-in-async", "medium", "orders.handleOrder", 12),
            ("blocking-in-async", "medium", "orders.Service.run", 27),
            ("io-in-loop", "medium", "orders.handleOrder", 14),  # prisma N+1
            ("io-without-timeout", "medium", "orders.handleOrder", 16),
            ("io-in-loop", "medium", "orders.refresh", 22),
            ("io-without-timeout", "medium", "orders.refresh", 22),
        ],
        "absent": [("io-without-timeout", 17), ("io-without-timeout", 18)],
        "chain": ("orders.Service.run", 27, ["orders.Service.run", "orders.Service.helper"]),
    },
    "go": {
        "present": [
            ("io-in-loop", "medium", "Store.LoadUsers", 13),
            ("io-without-timeout", "medium", "fetch", 18),
            ("io-in-loop", "medium", "fanOut", 28),
            ("unbounded-concurrency", "medium", "fanOut", 28),
            ("defer-in-loop", "low", "fanOut", 29),
            ("io-in-loop", "low", "main", 35),  # `for {}` service loop
        ],
        "absent": [("io-without-timeout", 23)],
    },
    "java": {
        "present": [
            ("io-in-loop", "medium", "com.x.OrderService.load", 14),
            ("io-in-loop", "medium", "com.x.OrderService.load", 16),  # forEach lambda -> enrich -> send
            ("io-without-timeout", "medium", "com.x.OrderService.enrich", 21),
            ("nested-loops", "info", "com.x.OrderService.grid", 24),
        ],
    },
    "c": {
        "present": [
            ("io-without-timeout", "medium", "fetch::fetch", 5),
            ("io-in-loop", "medium", "fetch::poll_all", 10),
        ],
        "absent_fn": [("io-without-timeout", "bounded::fetch_bounded")],
    },
    "cpp": {
        "present": [("nested-loops", "info", "engine::eng::Engine::run", 14)],
        "boundaries": {("engine::eng::Engine::step", "sleep"), ("engine::eng::Engine::step", "wait")},
    },
}


@pytest.fixture(scope="module")
def maps():
    return {}


def _load(maps, lang):
    if lang not in maps:
        maps[lang] = RepoMap(FIXTURES / "lang" / lang).load()
    return maps[lang]


@pytest.mark.parametrize("lang", sorted(CASES))
def test_language(lang, maps):
    m = _load(maps, lang)
    case = CASES[lang]
    assert not m.parse_errors
    got = {(f.rule, f.severity, f.function, f.line) for f in m.findings}
    missing = [p for p in case.get("present", []) if p not in got]
    assert not missing, f"missing {missing}\ngot {sorted(got)}"
    for rule, line in case.get("absent", []):
        assert not any(f.rule == rule and f.line == line for f in m.findings), (rule, line)
    for rule, fn in case.get("absent_fn", []):
        assert not any(f.rule == rule and f.function == fn for f in m.findings), (rule, fn)
    if "chain" in case:
        fn, line, prefix = case["chain"]
        chains = [f.chain for f in m.findings if f.function == fn and f.line == line and f.chain]
        assert any(c[:len(prefix)] == prefix for c in chains), chains
    if "boundaries" in case:
        kinds = {(m.functions[b.function].qualname, b.kind) for b in m.boundaries}
        assert case["boundaries"] <= kinds


def test_no_high_findings_without_async_support(maps):
    for lang in ("go", "java", "c", "cpp"):
        assert not any(f.rule == "blocking-in-async" for f in _load(maps, lang).findings)


def test_tests_are_skipped_by_default(tmp_path):
    (tmp_path / "tests").mkdir()
    (tmp_path / "tests" / "test_x.py").write_text("import time\nasync def t():\n    time.sleep(1)\n")
    (tmp_path / "app.py").write_text("def f():\n    return 1\n")
    assert list(RepoMap(tmp_path).load().files) == ["app.py"]
    assert "tests/test_x.py" in RepoMap(tmp_path, include_tests=True).load().files
