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
    assert q["items"][0]["severity"] == "high"
    assert q["items"][0]["score"] >= q["items"][1]["score"]
    # One investigation per function: every queued finding of that function rides along in `findings`.
    assert len({it["function"] for it in q["items"]}) == 2
    for it in q["items"]:
        assert it["findings"][0]["id"] == it["id"]
        assert it["repo"] == str(audit.parent) and it["abs_file"].startswith(str(audit.parent))
        assert f"{it['line']:>5}" in it["source"]  # the real code, so a wrong tree is obvious
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
    for lang, want in (("rust", "native probe or black-box"), ("go", "native probe or black-box")):
        repo = tmp_path / lang
        shutil.copytree(FIXTURES / "effects" / lang, repo)
        report.write(RepoMap(repo).load(), repo / ".audit")
        q = orchestrate.queue(repo / ".audit", {"total": 3, "per_round": 3})
        assert q["items"] and all(it["lang"] == lang and it["harness"].startswith(want) for it in q["items"])
        assert not any("harness" in s["reason"] for s in q["skipped"])


def test_harness_for_python_and_unscaffolded_languages():
    assert orchestrate.harness_for("python") == "in-process (repro/harness.py), or black-box"
    assert orchestrate.harness_for("cobol") == "black-box"  # no scaffold: falls back to black-box only


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
    assert data["effects"]["deadlines"] and data["meta"]["investigated"] >= 2
    via = [f for f in data["findings"] if (f.get("verdict") or {}).get("via")]
    assert all(f["verdict"]["via"] in (proven, rejected) for f in via)  # same-effect findings share the verdict
    paths = report_html.write(data, audit)
    page = paths[2].read_text(encoding="utf8")
    assert page.startswith('<meta charset="utf-8">\n<title>repo audit</title>')
    assert "<html" not in page and "<body" not in page  # artifact contract: no document wrapper
    assert "</script>" not in page.split('id="argus-data">')[1].split("</script>")[0]  # data can't break out
    md = paths[1].read_text(encoding="utf8")
    assert "## Proven (1)" in md and "the loop freezes for 30 s" in md


def test_function_grouping_and_same_effect_verdicts(audit):
    q = orchestrate.queue(audit, {"total": 20, "per_round": 20})
    fns = [it["function"] for it in q["items"]]
    assert len(fns) == len(set(fns))
    issued = {f["id"] for it in q["items"] for f in it["findings"]}
    # Nothing issued comes back next round, whether it was the primary or rode along.
    orchestrate.record(audit, [{"finding": i, "verdict": "rejected", "reason": "x"} for i in issued])
    q2 = orchestrate.queue(audit, dry_run=True)
    assert not issued & {it["id"] for it in q2["items"]}


def test_tooling_inconclusive_is_retried_once(audit):
    fid = orchestrate.queue(audit, {"total": 10, "per_round": 1})["items"][0]["id"]
    orchestrate.record(audit, [{"finding": fid, "verdict": "inconclusive", "blocked_by": "wrong-tree",
                                "reason": "read the sibling directory"}])
    q = orchestrate.queue(audit, {"per_round": 10})
    assert fid in {it["id"] for it in q["items"]}
    orchestrate.record(audit, [{"finding": fid, "verdict": "inconclusive", "blocked_by": "wrong-tree"}])
    led = json.loads((audit / "verdicts.json").read_text())
    assert led["verdicts"][fid]["attempts"] == 2
    assert fid not in {it["id"] for it in orchestrate.queue(audit, dry_run=True)["items"]}


def test_confirmed_checks_its_own_repro_file(audit):
    fid = orchestrate.queue(audit, {"total": 4, "per_round": 1})["items"][0]["id"]
    # The test crashed before it could print its finding id: matched by file, and it failed.
    (audit / "repro.json").write_text(json.dumps({"tests": [
        {"file": "test_mine.py", "test": "test_a", "outcome": "failed", "time_s": 0, "message": "boom",
         "evidence": [], "finding": None}], "exit_code": 1, "output_tail": ""}))
    orchestrate.record(audit, [{"finding": fid, "verdict": "confirmed", "repro_file": ".audit/repros/test_mine.py"}])
    v = json.loads((audit / "verdicts.json").read_text())["verdicts"][fid]
    assert v["verdict"] == "inconclusive" and v["repro_check"] == "failed" and v["blocked_by"] == "repro-check"
