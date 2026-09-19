"""Sizes flowing downstream: a quadratic callee projected to the largest input seen upstream."""
from auditor.trace.evidence import Evidence, to_markdown
from auditor.trace.fit import fit_power
from auditor.trace.store import CallRec, Trace

MAP = {
    "functions": [{"id": "app/x.py:1:0", "qualname": "app.x.batch"},
                  {"id": "app/x.py:10:0", "qualname": "app.x.matrix"}],
    "findings": [],
}


def trace(sizes=(10, 100, 1000, 3000), upstream_only=50000):
    calls, i = [], 0

    def rec(parent, line, name, shapes, dur):
        nonlocal i
        i += 1
        calls.append(CallRec(1, i, parent, 1, "app/x.py", line, name, False, 0.0, dur, dur, 1, dur, shapes))
        return i

    for n in sizes:
        for _ in range(3):
            p = rec(0, 1, "batch", {"items": float(n)}, 1e-7 * n * n + 0.001)
            rec(p, 10, "matrix", {"rows": float(n)}, 1e-7 * n * n)
    if upstream_only:  # batch saw a big input but returned before calling matrix
        rec(0, 1, "batch", {"items": float(upstream_only)}, 0.002)
    return Trace([{"stall_threshold": "0.1"}], calls, [])


def test_fit_predicts_its_own_points():
    fit = fit_power([(n, 1e-7 * n * n) for n in (10, 100, 1000, 3000)])
    assert fit.exponent == 2.0
    assert abs(fit.predict(3000) - 0.9) / 0.9 < 0.05


def test_upstream_size_projects_downstream_cost():
    proj = Evidence(MAP, trace()).build()["projections"]
    [p] = proj
    assert p["function"] == "app.x.matrix" and p["arg"] == "rows" and p["exponent"] == 2.0
    [row] = p["projections"]
    assert row["n"] == 50000 and "app.x.batch(items)" in row["source"]
    assert 200 < row["predicted_s"] < 300  # 1e-7 * 50000^2 = 250 s
    assert p["risk"] is True


def test_assume_hint_and_markdown():
    data = Evidence(MAP, trace(upstream_only=0), {"rows": 100000}).build()
    [p] = data["projections"]
    assert p["projections"][0]["source"] == "--assume rows=100000"
    assert 900 < p["projections"][0]["predicted_s"] < 1100
    md = to_markdown({**data, "meta": {**data["meta"], "assume": {"rows": 100000}}})
    assert "## Scaling projections" in md and "rows=100000" in md


def test_no_projection_without_a_larger_input():
    assert Evidence(MAP, trace(upstream_only=0)).build()["projections"] == []
