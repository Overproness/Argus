"""Pre-filled reproduction skeletons: every template is valid Python, and queue() writes them without
ever overwriting an investigator's work in progress."""
import ast
import shutil

from conftest import FIXTURES

from auditor import orchestrate, report
from auditor.analysis import RepoMap
from auditor.repro import scaffold


def test_every_template_renders_valid_python():
    for rule in list(scaffold.TEMPLATES) + ["some-unmodeled-rule"]:
        item = {"function": "app.get_job", "line": 10,
                "findings": [{"id": f"{rule}@app.get_job:10", "rule": rule, "line": 10, "message": "m"}]}
        ast.parse(scaffold.generate(item))  # raises SyntaxError if the template is broken


def test_propagated_rule_uses_the_base_rule_template():
    item = {"function": "app.handler", "line": 1,
            "findings": [{"id": "propagated:sql-injection@app.handler:1", "rule": "propagated:sql-injection",
                          "line": 1, "message": "m"}]}
    assert "sqlite3" in scaffold.generate(item)  # the sql-injection template, not the generic fallback


def test_non_python_function_ids_give_a_valid_filename_and_def(tmp_path):
    # Rust/Go/C++ ids contain `::`, `/`, `(*T)`: the skeleton's filename must exist on Windows and its `def` parse.
    for fid, name in (("feed::tick", "tick"), ("crate::feed::Feed::new", "new"),
                      ("pkg/mod.(*Client).Do", "Do"), ("ns::run<T>", "run_T_")):
        item = {"function": fid, "line": 3,
                "findings": [{"id": f"io-without-timeout@{fid}:3", "rule": "io-without-timeout",
                              "line": 3, "message": "m"}]}
        p = scaffold.write(item, tmp_path)
        assert p.name == f"test_{name}.py" and p.exists()
        ast.parse(p.read_text(encoding="utf8"))


def test_write_never_overwrites_an_existing_file(tmp_path):
    item = {"function": "app.get_job", "line": 10,
            "findings": [{"id": "io-without-timeout@app.get_job:10", "rule": "io-without-timeout",
                          "line": 10, "message": "m"}]}
    p1 = scaffold.write(item, tmp_path)
    p1.write_text("# the investigator's own work\n")
    p2 = scaffold.write(item, tmp_path)
    assert p1 == p2 and p2.read_text() == "# the investigator's own work\n"


def test_queue_writes_a_skeleton_per_item(tmp_path):
    repo = tmp_path / "repo"
    shutil.copytree(FIXTURES / "py_concurrency", repo)
    out = repo / ".audit"
    report.write(RepoMap(repo).load(), out)
    q = orchestrate.queue(out, {"total": 3, "per_round": 3})
    for it in q["items"]:
        p = out / "repros" / f"{it['repro_skeleton'].rsplit('/', 1)[-1]}"
        assert p.exists()
        ast.parse(p.read_text())
        assert it["findings"][0]["id"] in p.read_text()
