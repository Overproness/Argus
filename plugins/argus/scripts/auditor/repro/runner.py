"""Run reproduction tests and collect their outcomes and evidence.

Reproductions live in `<out>/repros/test_*.py`. Each is a pytest file; the
`harness` helpers print `@@evidence {...}` lines, which pytest captures per test
into the JUnit XML this runner parses. A test that *fails* when the predicted
effect appears is the wrong shape: write assertions so that **passing means
the finding is reproduced**, and use `evidence(...)` for the numbers.
"""
from __future__ import annotations

import json
import os
import subprocess
import sys
import xml.etree.ElementTree as ET
from datetime import datetime, timezone
from pathlib import Path

from .harness import EVIDENCE_PREFIX

SCRIPTS_DIR = Path(__file__).resolve().parents[2]


def run_pytest(repo: Path, files: list[Path], junit: Path, timeout: int) -> tuple[int, str]:
    env = dict(os.environ)
    env["PYTHONPATH"] = os.pathsep.join([str(SCRIPTS_DIR), str(repo)] + (
        [env["PYTHONPATH"]] if env.get("PYTHONPATH") else []))
    env["AUDIT_REPRO"] = "1"
    cmd = [sys.executable, "-m", "pytest", "-q", "-p", "no:cacheprovider", "--tb=short", "-o", "addopts=",
           "-o", "junit_logging=system-out", f"--junitxml={junit}", *map(str, files)]
    try:
        r = subprocess.run(cmd, cwd=repo, env=env, capture_output=True, text=True, timeout=timeout)
    except subprocess.TimeoutExpired as e:
        return 124, f"timed out after {timeout}s\n{(e.stdout or '')[-2000:]}"
    return r.returncode, (r.stdout + r.stderr)[-4000:]


def parse_junit(path: Path) -> list[dict]:
    if not path.exists():
        return []
    out = []
    for tc in ET.parse(path).getroot().iter("testcase"):
        outcome, message = "passed", ""
        for tag, name in (("failure", "failed"), ("error", "error"), ("skipped", "skipped")):
            node = tc.find(tag)
            if node is not None:
                outcome, message = name, (node.get("message") or (node.text or ""))[:800]
                break
        stdout = tc.findtext("system-out") or ""
        ev = []
        for line in stdout.splitlines():
            if line.startswith(EVIDENCE_PREFIX):
                try:
                    ev.append(json.loads(line[len(EVIDENCE_PREFIX):]))
                except json.JSONDecodeError:
                    pass
        out.append({
            "file": tc.get("classname", "").split(".")[-1] + ".py",
            "test": tc.get("name"), "outcome": outcome, "time_s": float(tc.get("time") or 0),
            "message": message, "evidence": ev,
            "finding": next((e["finding"] for e in ev if "finding" in e), None),
        })
    return out


def run(repo: Path, out_dir: Path, target: Path | None = None, timeout: int = 600) -> dict:
    repro_dir = out_dir / "repros"
    repro_dir.mkdir(parents=True, exist_ok=True)
    files = [target] if target else sorted(repro_dir.glob("test_*.py"))
    result = {
        "meta": {"tool": "argus/repro", "generated_at": datetime.now(timezone.utc).isoformat(timespec="seconds"),
                 "files": [str(f) for f in files]},
        "tests": [], "exit_code": 0, "output_tail": "",
    }
    if not files:
        result["output_tail"] = f"no test_*.py under {repro_dir}"
        return result
    junit = repro_dir / "results.xml"
    if junit.exists():
        junit.unlink()
    rc, tail = run_pytest(repo, files, junit, timeout)
    result["exit_code"], result["output_tail"] = rc, tail
    result["tests"] = parse_junit(junit)
    return result


def to_markdown(d: dict) -> str:
    out = ["# Reproduction results\n"]
    tests = d["tests"]
    counts = {}
    for t in tests:
        counts[t["outcome"]] = counts.get(t["outcome"], 0) + 1
    out.append(", ".join(f"{v} {k}" for k, v in sorted(counts.items())) or "no tests ran")
    out.append("\n_A passing test means the reproduction triggered the predicted effect. "
               "Evidence lines are what the harness measured._\n")
    for t in tests:
        out.append(f"## {t['test']} — **{t['outcome']}**\n")
        if t["finding"]:
            out.append(f"Finding: `{t['finding']}`  ")
        out.append(f"File: `{t['file']}` · {t['time_s']:.2f} s\n")
        for e in t["evidence"]:
            out.append(f"- " + ", ".join(f"{k}={json.dumps(v)}" for k, v in e.items() if k != "finding"))
        if t["message"]:
            out.append(f"\n```\n{t['message']}\n```")
        out.append("")
    if d["exit_code"] not in (0, 1) and d["output_tail"]:
        out.append(f"## Runner output\n\n```\n{d['output_tail']}\n```\n")
    return "\n".join(out)


def write(d: dict, out_dir: Path) -> tuple[Path, Path]:
    jp, mp = out_dir / "repro.json", out_dir / "repro.md"
    jp.write_text(json.dumps(d, indent=2), encoding="utf8")
    mp.write_text(to_markdown(d), encoding="utf8")
    return jp, mp
