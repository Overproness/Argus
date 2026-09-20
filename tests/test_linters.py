"""SARIF import: results land in map functions and corroborate equivalent map findings."""
import json
import subprocess
import sys

from conftest import FIXTURES, ROOT

CLI = [sys.executable, str(ROOT / "plugins" / "argus" / "scripts" / "auditor_cli.py")]
FIX = FIXTURES / "rust_trader"


def sarif(results):
    return {"version": "2.1.0", "runs": [{"tool": {"driver": {"name": "clippy", "rules": []}}, "results": results}]}


def result(rule, uri, line, level="warning", msg="m"):
    return {"ruleId": rule, "level": level, "message": {"text": msg},
            "locations": [{"physicalLocation": {"artifactLocation": {"uri": uri}, "region": {"startLine": line}}}]}


def test_sarif_corroborates_and_leads(tmp_path):
    subprocess.run([*CLI, "map", FIX, "--out", tmp_path], check=True, capture_output=True)
    f = tmp_path / "x.sarif"
    f.write_text(json.dumps(sarif([
        result("clippy::await_holding_lock", f"file://{FIX}/src/strategy.rs", 26),  # lock-across-await in run_strategy
        result("clippy::needless_return", "src/risk.rs", 8, "note"),                # unrelated lead
        result("clippy::await_holding_lock", "src/elsewhere.rs", 3),                # no such file: still a lead
    ])))
    r = subprocess.run([*CLI, "lint-import", FIX, "--out", tmp_path, "--sarif", f], capture_output=True, text=True)
    assert r.returncode == 0, r.stderr
    d = json.loads((tmp_path / "lint.json").read_text())
    assert [(c["function"], c["rule"]) for c in d["corroborated"]] == [("rust_trader::strategy::run_strategy", "lock-across-await")]
    assert d["totals"] == {"results": 3, "corroborated_findings": 1, "leads": 2}
    assert any(x["function"] == "rust_trader::risk::exposure" for x in d["leads"])


def test_not_sarif_is_an_error(tmp_path):
    subprocess.run([*CLI, "map", FIX, "--out", tmp_path], check=True, capture_output=True)
    f = tmp_path / "bad.json"
    f.write_text("{}")
    r = subprocess.run([*CLI, "lint-import", FIX, "--out", tmp_path, "--sarif", f], capture_output=True, text=True)
    assert r.returncode == 1 and "not a SARIF" in r.stderr
