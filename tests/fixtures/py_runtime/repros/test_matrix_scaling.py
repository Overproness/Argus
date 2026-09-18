"""Reproduction: nested-loops at app/service.py:20 (matrix). Expected: O(n^2)."""
from auditor.repro.harness import evidence, scaling

from app import service

FINDING = "nested-loops@app.service.matrix:20"


def test_matrix_is_quadratic():
    fit = scaling(service.matrix, [50, 100, 200, 400, 800], lambda n: list(range(n)))
    evidence(finding=FINDING, exponent=fit.exponent if fit else None)
    assert fit is not None and fit.exponent >= 1.7
