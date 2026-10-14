"""Dataset integrity, proof orientation and answer-key isolation."""
import importlib.util
import json
import subprocess
import sys
import zipfile
from pathlib import Path

import pytest

BASE = Path(__file__).resolve().parents[1] / "benchmarks/relaybench"


def module(name, file):
    spec = importlib.util.spec_from_file_location(name, file)
    loaded = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(loaded)
    return loaded


@pytest.fixture(scope="module")
def bundle(tmp_path_factory):
    sys.path.insert(0, str(BASE))
    builder = module("relaybench_builder", BASE / "build.py")
    root = tmp_path_factory.mktemp("relaybench") / "dataset"
    manifest = builder.build(root, seed=7)
    return root, manifest


def test_each_fix_is_the_immediate_child_and_only_changes_its_seed(bundle):
    root, manifest = bundle
    assert len(manifest["cases"]) == 14 and len(manifest["samples"]) == 30
    for case in manifest["cases"]:
        history = root / "judge/history"
        parent = subprocess.check_output(["git", "-C", str(history), "rev-parse", case["fix_commit"] + "^"], text=True).strip()
        assert parent == case["buggy_commit"]
        changed = subprocess.check_output(["git", "-C", str(history), "diff", "--name-only", parent, case["fix_commit"]], text=True).splitlines()
        assert changed == [case["file"]]


def test_source_pack_has_no_answer_key_or_git_history(bundle, tmp_path):
    root, manifest = bundle
    packer = module("relaybench_packer", BASE / "pack.py")
    sample = next(s for s in manifest["samples"] if s["role"] == "mixed")
    output = tmp_path / "target.zip"
    count = packer.pack(root, sample["id"], output)
    with zipfile.ZipFile(output) as archive:
        assert count == len(sample["files"])
        assert set(archive.namelist()) == set(sample["files"])
        assert all(not any(part in {"judge", ".git", ".audit", "target", "__pycache__"} for part in Path(name).parts) for name in archive.namelist())
        assert "relay/service.py" in archive.namelist() and "edge/Cargo.lock" in archive.namelist()
        assert {entry.date_time for entry in archive.infolist()} == {(2026, 1, 1, 0, 0, 0)}


def test_changed_source_is_refused_but_audit_artifacts_are_allowed(bundle, tmp_path):
    root, manifest = bundle
    verifier = module("relaybench_verifier", BASE / "verify.py")
    sample = next(s for s in manifest["samples"] if s["role"] == "clean")
    audit = root / sample["path"] / ".audit"
    audit.mkdir()
    (audit / "result.json").write_text("{}")
    verifier.check_integrity(root, sample)
    source = root / sample["path"] / "relay/money.py"
    original = source.read_bytes()
    try:
        source.write_bytes(original + b"\n# changed\n")
        with pytest.raises(ValueError, match="Source changed"):
            verifier.check_integrity(root, sample)
    finally:
        source.write_bytes(original)


def test_receipt_contract_fails_on_buggy_and_passes_on_fixed(bundle):
    root, manifest = bundle
    verifier = module("relaybench_verifier_proof", BASE / "verify.py")
    case = next(c for c in manifest["cases"] if c["id"] == "p04")
    samples = {s["id"]: s for s in manifest["samples"]}
    before = verifier.run_oracle(root, samples[case["buggy_sample"]], case)
    after = verifier.run_oracle(root, samples[case["fixed_sample"]], case)
    assert before["exit_code"] == 1 and before["metrics"]["persisted"] is None
    assert after["exit_code"] == 0 and after["metrics"]["persisted"] == "delivered"


def test_setup_error_is_not_a_contract_violation(bundle):
    root, manifest = bundle
    verifier = module("relaybench_verifier_error", BASE / "verify.py")
    sample = next(s for s in manifest["samples"] if s["role"] == "clean")
    result = verifier.run_oracle(root, sample, dict(language="python", oracle="nonexistent"))
    assert result["exit_code"] == 2 and "error" in result


def test_changed_oracle_hash_is_refused_before_executing_code(bundle):
    root, manifest = bundle
    verifier = module("relaybench_verifier_hash", BASE / "verify.py")
    path = root / "judge/manifest.json"
    original = path.read_bytes()
    altered = json.loads(original)
    altered["oracle_sha256"]["python_oracle.py"] = "0" * 64
    try:
        path.write_text(json.dumps(altered), encoding="utf-8")
        with pytest.raises(ValueError, match="Oracle changed"):
            verifier.verify(root, case_ids=["p04"])
    finally:
        path.write_bytes(original)


def test_score_match_requires_rule_function_and_file():
    sys.path.insert(0, str(BASE))
    scorer = module("relaybench_score", BASE / "score.py")
    wanted = dict(rule="blocking-in-async", function="relay.gateway.InventoryClient.fetch_inventory", file="relay/gateway.py")
    assert scorer.match(wanted, wanted)
    assert not scorer.match({**wanted, "file": "other/gateway.py"}, wanted)
    assert not scorer.match({**wanted, "rule": "io-in-loop"}, wanted)
    assert not scorer.match({**wanted, "function": "relay.gateway.other"}, wanted)


def test_queue_coverage_includes_grouped_same_effect_ids():
    sys.path.insert(0, str(BASE))
    scorer = module("relaybench_score_queue", BASE / "score.py")
    items = [{"id": "item", "findings": [{"id": "primary", "same_effect": ["covered"]}, {"id": "rider"}]}]
    assert scorer.queue_ids(items) == {"primary", "covered", "rider"}
