"""Python trace filtering is relative to the target repo, including repos under .audit."""
import pytest

from auditor.trace.py.tracer import Tracer


def function_code(path):
    return compile("def work():\n    return 1\n", str(path), "exec").co_consts[0]


@pytest.mark.parametrize("ancestor", [".audit", ".venv", "venv", "site-packages", "dist-packages", "node_modules"])
def test_repo_under_ignored_ancestor_is_traced(tmp_path, ancestor):
    repo = tmp_path / ancestor / "repo"
    tracer = Tracer(repo, None, 0.1, False)

    assert tracer.info(function_code(repo / "app.py")) == ("app.py", 1, False)


@pytest.mark.parametrize("relative", [
    ".audit/repro.py", "nested/.audit/repro.py", ".venv/lib/pkg.py", "venv/lib/pkg.py",
    "site-packages/pkg.py", "dist-packages/pkg.py", "node_modules/pkg/index.py",
])
def test_generated_and_dependency_code_inside_repo_is_ignored(tmp_path, relative):
    tracer = Tracer(tmp_path, None, 0.1, False)

    assert tracer.info(function_code(tmp_path / relative)) is None


def test_code_outside_repo_is_ignored(tmp_path):
    tracer = Tracer(tmp_path / "repo", None, 0.1, False)

    assert tracer.info(function_code(tmp_path / "repo-sibling" / "app.py")) is None
