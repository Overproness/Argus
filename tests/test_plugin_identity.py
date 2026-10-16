"""Prevent identical cache versions from hiding changed plugin payloads."""
import json
import shutil
import subprocess
import sys

from conftest import ROOT
from auditor import identity, orchestrate, report
from auditor.analysis import RepoMap


PLUGIN = ROOT / "plugins" / "argus"


def test_git_marketplace_uses_commit_versions():
    marketplace = json.loads((ROOT / ".claude-plugin/marketplace.json").read_text(encoding="utf8"))
    entry = next(p for p in marketplace["plugins"] if p["name"] == "argus")
    manifest = json.loads((PLUGIN / ".claude-plugin/plugin.json").read_text(encoding="utf8"))
    assert entry["source"] == "./plugins/argus"
    # Both override Claude Code's Git-SHA fallback. Reusing 0.2.0 froze old installs.
    assert "version" not in manifest and "version" not in entry


def test_fingerprint_survives_copy_and_ignores_runtime_artifacts(tmp_path):
    copied = tmp_path / "another installation"
    shutil.copytree(PLUGIN, copied, ignore=shutil.ignore_patterns("__pycache__", "*.pyc"))
    before = identity.fingerprint(PLUGIN)
    assert identity.fingerprint(copied) == before
    cache = copied / "scripts/__pycache__"
    cache.mkdir(exist_ok=True)
    (cache / "foo.pyc").write_bytes(b"runtime cache")
    (copied / ".env").write_text("not part of the shipped payload")
    assert identity.fingerprint(copied) == before
    agent = copied / "agents/investigator.md"
    agent.write_bytes(agent.read_bytes() + b"\nUpdated instructions.\n")
    assert identity.fingerprint(copied) != before


def test_running_process_reports_files_changed_since_startup(tmp_path, monkeypatch):
    root = tmp_path / "plugin"
    (root / ".claude-plugin").mkdir(parents=True)
    (root / ".claude-plugin/plugin.json").write_text('{"name": "argus"}')
    (root / "scripts").mkdir()
    code = root / "scripts/tool.py"
    code.write_text("old = True\n")
    loaded = identity.fingerprint(root)
    monkeypatch.setattr(identity, "PLUGIN_ROOT", root)
    monkeypatch.setattr(identity, "LOADED_SHA256", loaded)
    assert not identity.runtime_identity()["restart_required"]
    code.write_text("old = False\n")
    status = identity.runtime_identity()
    assert status["restart_required"]
    assert status["loaded_plugin_sha256"] == loaded
    assert status["plugin_sha256"] != loaded


def test_doctor_works_without_site_packages_or_a_repo(tmp_path):
    result = subprocess.run(
        [sys.executable, "-I", "-S", str(PLUGIN / "scripts/auditor_cli.py"), "doctor"],
        cwd=tmp_path, capture_output=True, text=True, timeout=30,
    )
    assert result.returncode == 0, result.stderr
    status = json.loads(result.stdout)
    assert status["plugin_root"] == str(PLUGIN)
    assert status["plugin_sha256"] == identity.fingerprint(PLUGIN)
    assert status["loaded_plugin_sha256"] == status["plugin_sha256"]
    assert status["declared_version"] is None
    assert not status["restart_required"]
    assert list(tmp_path.iterdir()) == []


def test_report_preserves_mapper_identity_when_reporter_differs(tmp_path):
    repo = tmp_path / "app"
    repo.mkdir()
    (repo / "app.py").write_text("def hello():\n    return 'hello'\n")
    out = repo / ".audit"
    map_file, _ = report.write(RepoMap(repo).load(), out)
    data = json.loads(map_file.read_text(encoding="utf8"))
    assert data["meta"]["argus"]["plugin_sha256"] == identity.fingerprint(PLUGIN)
    data["meta"]["argus"]["plugin_sha256"] = "different mapper payload"
    map_file.write_text(json.dumps(data), encoding="utf8")
    result = orchestrate.final(out)
    assert result["meta"]["map_argus"]["plugin_sha256"] == "different mapper payload"
    assert result["meta"]["argus"]["plugin_sha256"] == identity.fingerprint(PLUGIN)
