"""score_investigation.py: scores a real (or faked, for the test) investigation run against the ledger
and, optionally, a corpus case's ground truth. Excluded from `pytest tests`' directory recursion (see
conftest.py's collect_ignore_glob for tests/eval); run it by passing this file explicitly:
`pytest tests/eval/test_score_investigation.py`."""
import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
import score_investigation as scorer


def _audit(tmp_path, verdicts, rounds):
    out = tmp_path / ".audit"
    out.mkdir()
    (out / "verdicts.json").write_text(json.dumps({
        "budget": {"total": len(rounds[0]["items"]) if rounds else 0},
        "rounds": rounds, "verdicts": verdicts,
    }))
    return out


def test_tokens_total_mode(tmp_path):
    out = _audit(tmp_path, {
        "a@f:1": {"verdict": "confirmed"}, "b@g:2": {"verdict": "confirmed"}, "c@h:3": {"verdict": "rejected"},
    }, [{"round": 1, "items": ["a@f:1", "b@g:2", "c@h:3"]}])
    r = scorer.score(out, 90000, 3, None, None, None)
    assert r["investigations"] == 3 and r["proven"] == 2
    assert r["tokens_per_investigation"] == 30000 and r["tokens_per_proven_finding"] == 45000


def test_tokens_file_mode(tmp_path):
    out = _audit(tmp_path, {"a@f:1": {"verdict": "confirmed"}}, [{"round": 1, "items": ["a@f:1"]}])
    costs = tmp_path / "costs.json"
    costs.write_text(json.dumps({"a@f:1": 40000}))
    r = scorer.score(out, None, None, costs, None, None)
    assert r["tokens_total"] == 40000 and r["investigations"] == 1 and r["tokens_per_proven_finding"] == 40000


def test_corpus_recall_after_investigation(tmp_path):
    out = _audit(tmp_path, {
        "blocking-in-async@shop.jobs.submit_batch:37": {"verdict": "confirmed"},
        "unbounded-wait-loop@shop.jobs.get_job:46": {"verdict": "confirmed"},
    }, [{"round": 1, "items": ["blocking-in-async@shop.jobs.submit_batch:37",
                               "unbounded-wait-loop@shop.jobs.get_job:46"]}])
    r = scorer.score(out, None, None, None,
                     Path(__file__).resolve().parent / "corpus.yaml", "orderdesk-seeded-service")
    assert r["corpus_proven"] == 2
    assert "unbounded-module-container@shop.jobs.create_job" in r["corpus_missed"]


def test_missing_audit_dir_raises(tmp_path):
    import pytest
    with pytest.raises(FileNotFoundError):
        scorer.score(tmp_path / "nope", None, None, None, None, None)


def test_one_batch_does_not_turn_six_function_slots_into_six_agent_costs(tmp_path):
    out = _audit(tmp_path, {}, [{"round": 1, "items": list("abcdef"),
                               "batches": [{"item_ids": list("abcdef")}]}])
    result = scorer.score(out, 12000, None, None, None, None)
    assert result["investigator_runs"] == 1 and result["function_investigations_issued"] == 6
    assert result["tokens_per_investigation"] == 12000
    assert result["tokens_per_function_investigation"] == 2000


def test_scoring_does_not_count_a_confirmation_invalidated_by_latest_repro(tmp_path):
    out = _audit(tmp_path, {"rule@f:1": {"verdict": "confirmed"}}, [{"items": ["rule@f:1"]}])
    (out / "repro.json").write_text(json.dumps({"tests": [
        {"finding": "rule@f:1", "outcome": "failed"}]}))
    result = scorer.score(out, None, None, None, None, None)
    assert result["proven"] == 0 and result["by_verdict"]["inconclusive"] == 1
