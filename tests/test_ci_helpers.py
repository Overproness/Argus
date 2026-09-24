"""ci_helpers.py: the two entry points action.yml calls (kept as real scripts, not YAML heredocs, so they
can be tested and never fall victim to block-scalar indentation breaking Python's syntax)."""
import json
import subprocess
import sys

from conftest import FIXTURES, ROOT

SCRIPT = ROOT / "plugins" / "argus" / "scripts" / "ci_helpers.py"


def run(*args, env=None):
    return subprocess.run([sys.executable, SCRIPT, *args], capture_output=True, text=True, env=env)


def test_outputs_without_github_output_env_prints_to_stdout(tmp_path):
    mj = tmp_path / "map.json"
    mj.write_text(json.dumps({"findings": [{"severity": "high"}, {"severity": "low"}, {"severity": "low"}]}))
    r = run("outputs", str(mj), env={})
    assert r.returncode == 0
    lines = dict(line.split("=", 1) for line in r.stdout.strip().splitlines())
    assert lines["high"] == "1" and lines["low"] == "2" and lines["medium"] == "0"
    assert lines["map_json"].endswith("map.json")


def test_outputs_writes_to_github_output_file(tmp_path):
    mj = tmp_path / "map.json"
    mj.write_text(json.dumps({"findings": [{"severity": "medium"}]}))
    out = tmp_path / "gh_output"
    r = run("outputs", str(mj), env={"GITHUB_OUTPUT": str(out)})
    assert r.returncode == 0 and r.stdout == ""
    lines = dict(line.split("=", 1) for line in out.read_text().strip().splitlines())
    assert lines["medium"] == "1"


def test_gate_fails_at_or_above_threshold(tmp_path):
    mj = tmp_path / "map.json"
    mj.write_text(json.dumps({"findings": [{"severity": "medium"}]}))
    ok = run("gate", str(mj), "high")
    assert ok.returncode == 0
    blocked = run("gate", str(mj), "medium")
    assert blocked.returncode == 1 and "::error::" in blocked.stdout


def test_gate_unknown_severity_is_a_usage_error(tmp_path):
    mj = tmp_path / "map.json"
    mj.write_text(json.dumps({"findings": []}))
    r = run("gate", str(mj), "critical")
    assert r.returncode == 2 and "unknown severity" in r.stderr


def test_real_map_against_the_gate(tmp_path):
    """End to end: a real map.json from a real fixture, not a hand-built one."""
    subprocess.run([sys.executable, ROOT / "plugins" / "argus" / "scripts" / "auditor_cli.py",
                    "map", FIXTURES / "rust_trader", "--out", tmp_path], check=True, capture_output=True)
    r = run("outputs", str(tmp_path / "map.json"), env={})
    lines = dict(line.split("=", 1) for line in r.stdout.strip().splitlines())
    assert int(lines["high"]) >= 1
    assert run("gate", str(tmp_path / "map.json"), "high").returncode == 1
