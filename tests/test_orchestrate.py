"""M4 orchestration: budgeted rounds, the verdict ledger, propagation between rounds, the final report."""
import json
import shutil

import pytest
from conftest import FIXTURES

from auditor import orchestrate, report, report_html
from auditor.analysis import RepoMap


@pytest.fixture()
def audit(tmp_path):
    repo = tmp_path / "repo"
    shutil.copytree(FIXTURES / "effects" / "python", repo)
    out = repo / ".audit"
    report.write(RepoMap(repo).load(), out)
    return out


def repro_json(out, *passing):
    tests = [{"file": "test_x.py", "test": f"test_{i}", "outcome": "passed", "time_s": 0.1,
              "message": "", "evidence": [{"finding": fid, "seen": 1}], "finding": fid}
             for i, fid in enumerate(passing)]
    (out / "repro.json").write_text(json.dumps({"tests": tests, "exit_code": 0, "output_tail": ""}))


def test_first_round_is_ranked_and_budgeted(audit):
    q = orchestrate.queue(audit, {"total": 4, "per_round": 2, "max_rounds": 3})
    assert q["round"] == 1 and len(q["items"]) == 2 and q["stop"] is None
    assert [it["severity"] for it in q["items"]] == ["high", "high"]
    assert q["items"][0]["score"] >= q["items"][1]["score"]
    assert q["budget"] == {"total": 4, "per_round": 2, "max_rounds": 3, "issued": 0, "remaining": 4}
    assert all(s["reason"] for s in q["skipped"])
    ledger = json.loads((audit / "verdicts.json").read_text())
    assert ledger["rounds"][0]["items"] == [it["id"] for it in q["items"]]
    # A dry run of the same state would open round 2, but nothing is written.
    assert orchestrate.queue(audit, dry_run=True)["round"] == 2
    assert len(json.loads((audit / "verdicts.json").read_text())["rounds"]) == 1


def test_confirmed_needs_a_passing_reproduction(audit):
    fid = orchestrate.queue(audit, {"total": 4, "per_round": 1})["items"][0]["id"]
    res = orchestrate.record(audit, [{"finding": fid, "verdict": "confirmed"}])
    assert res["downgraded"] == [fid]
    assert json.loads((audit / "verdicts.json").read_text())["verdicts"][fid]["verdict"] == "inconclusive"
    repro_json(audit, fid)
    orchestrate.record(audit, [{"finding": fid, "verdict": "confirmed", "repro_file": "test_x.py"}])
    v = json.loads((audit / "verdicts.json").read_text())["verdicts"][fid]
    assert v["verdict"] == "confirmed" and v["repro_check"] == "passed" and v["round"] == 1


def test_bad_input_is_refused(audit):
    res = orchestrate.record(audit, [{"finding": "nope@x:1", "verdict": "confirmed"}, {"verdict": "maybe"}])
    assert len(res["rejected_input"]) == 2 and not res["recorded"]


def test_confirmed_verdict_spawns_caller_items(audit):
    fid = "retry-without-backoff@svc.client.fetch_retrying:13"
    orchestrate.queue(audit, {"total": 10, "per_round": 10}, rules={"retry-without-backoff"})
    repro_json(audit, fid)
    orchestrate.record(audit, [{"finding": fid, "verdict": "confirmed", "severity": "medium",
                                "hypothesis": {"trigger": "every connect fails"}}])
    q = orchestrate.queue(audit, rules={"retry-without-backoff"})
    derived = [it for it in q["items"] if it["kind"] == "derived"]
    assert {it["function"] for it in derived} == {"svc.client.sync_all", "svc.client.handler"}
    assert all(it["given"]["confirmed"] == fid for it in derived)
    assert all(it["id"].startswith("propagated:retry-without-backoff@") for it in derived)


def test_rejected_edge_suppresses_dependent_findings(audit):
    orchestrate.queue(audit, {"total": 10, "per_round": 1})
    orchestrate.record(audit, [{"finding": "blocking-in-async@svc.client.blocking_handler:31", "verdict": "rejected",
                                "wrong_edge": ["svc.client.blocking_handler", "svc.client.fetch"],
                                "reason": "fetch resolves to another module"}])
    q = orchestrate.queue(audit)
    sup = {s["id"]: s["reason"] for s in q["skipped"] if s["reason"].startswith("suppressed")}
    assert "deadline-cannot-preempt@svc.client.guarded:35" in sup  # its chain goes through the rejected edge


def test_budget_and_round_caps_stop_the_loop(audit):
    q1 = orchestrate.queue(audit, {"total": 2, "per_round": 2, "max_rounds": 5})
    orchestrate.record(audit, [{"finding": it["id"], "verdict": "inconclusive"} for it in q1["items"]])
    assert orchestrate.queue(audit)["stop"] == "budget exhausted"
    orchestrate.queue(audit, {"total": 20, "max_rounds": 2})
    assert orchestrate.queue(audit)["stop"] == "max rounds reached"


def test_every_language_is_queued_with_its_harness(tmp_path):
    for lang, want in (("rust", "native probe or black-box"), ("go", "native probe (template not yet verified")):
        repo = tmp_path / lang
        shutil.copytree(FIXTURES / "effects" / lang, repo)
        report.write(RepoMap(repo).load(), repo / ".audit")
        q = orchestrate.queue(repo / ".audit", {"total": 3, "per_round": 3})
        assert q["items"] and all(it["lang"] == lang and it["harness"].startswith(want) for it in q["items"])
        assert not any("harness" in s["reason"] for s in q["skipped"])


def test_cli_round_trip(tmp_path):
    import subprocess
    import sys

    from conftest import ROOT
    cli = [sys.executable, str(ROOT / "plugins" / "argus" / "scripts" / "auditor_cli.py")]
    repo = tmp_path / "repo"
    shutil.copytree(FIXTURES / "effects" / "python", repo)

    def run(*args, stdin=None):
        r = subprocess.run(cli + list(args), capture_output=True, text=True, input=stdin, timeout=120)
        assert r.returncode == 0, r.stdout + r.stderr
        return r.stdout

    run("map", str(repo))
    out = run("queue", str(repo), "--budget", "1", "--per-round", "1")
    assert out.startswith("round 1: 1 item(s)")
    fid = json.loads((repo / ".audit" / "queue.json").read_text())["items"][0]["id"]
    assert "recorded 1" in run("record", str(repo), "--file", "-",
                               stdin=json.dumps({"finding": fid, "verdict": "inconclusive", "reason": "no mock"}))
    assert run("queue", str(repo)).startswith("stop: budget exhausted")
    out = run("report", str(repo))
    assert "1 inconclusive" in out and (repo / ".audit" / "report.html").exists()


def test_final_report(audit):
    q = orchestrate.queue(audit, {"total": 4, "per_round": 2})
    proven, rejected = q["items"][0]["id"], q["items"][1]["id"]
    repro_json(audit, proven)
    orchestrate.record(audit, [
        {"finding": proven, "verdict": "confirmed", "extreme_case": "the loop freezes for 30 s",
         "smallest_fix": "await asyncio.to_thread(fetch, url)", "repro_file": ".audit/repros/test_x.py"},
        {"finding": rejected, "verdict": "rejected", "reason": "startup only"}])
    data = orchestrate.final(audit)
    by_id = {f["id"]: f for f in data["findings"]}
    assert by_id[proven]["status"] == "proven" and by_id[rejected]["status"] == "rejected"
    assert data["findings"][0]["id"] == proven  # proven first
    assert data["summary"]["by_status"]["proven"] == 1
    assert data["effects"]["deadlines"] and data["meta"]["investigated"] == 2
    paths = report_html.write(data, audit)
    page = paths[2].read_text(encoding="utf8")
    assert page.startswith('<meta charset="utf-8">\n<title>repo audit</title>')
    assert "<html" not in page and "<body" not in page  # artifact contract: no document wrapper
    assert "</script>" not in page.split('id="argus-data">')[1].split("</script>")[0]  # data can't break out
    md = paths[1].read_text(encoding="utf8")
    assert "## Proven (1)" in md and "the loop freezes for 30 s" in md
