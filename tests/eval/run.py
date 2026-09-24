#!/usr/bin/env python3
"""Evaluation harness: how many of the ground-truth findings in corpus.yaml does Argus's static map
actually produce, on real (or locally fixtured) code -- not on the fixtures Argus's own unit tests use to
check its own logic, but on cases meant to answer "is this any good".

    python tests/eval/run.py                          # every case in corpus.yaml
    python tests/eval/run.py --case rust_trader-blocking-http
    python tests/eval/run.py --json out.json           # also write a machine-readable summary

Kept out of `pytest tests`: `git` cases clone a real repository, which is slow and needs network, so this
runs as its own (nightly) CI job instead. `pip install -r tests/eval/requirements.txt` first (PyYAML; kept
out of the plugin's own requirements.txt so installing Argus itself stays fast).

Exit code: 0 if every case has full recall and no `must_be_absent` hit; 1 otherwise, so a CI job can fail on
a real regression instead of a human having to read the report.
"""
from __future__ import annotations

import argparse
import json
import subprocess
import sys
from dataclasses import dataclass, field
from pathlib import Path

try:
    import yaml
except ImportError:
    print("eval: needs PyYAML: pip install -r tests/eval/requirements.txt", file=sys.stderr)
    sys.exit(2)

ROOT = Path(__file__).resolve().parents[2]
CLI = ROOT / "plugins" / "argus" / "scripts" / "auditor_cli.py"
CACHE = Path(__file__).resolve().parent / ".cache"


@dataclass
class CaseResult:
    case_id: str
    lang: str
    hits: list[dict] = field(default_factory=list)
    misses: list[dict] = field(default_factory=list)
    false_positives: list[dict] = field(default_factory=list)  # confirmed must_be_absent hits
    error: str | None = None

    @property
    def recall(self) -> float | None:
        total = len(self.hits) + len(self.misses)
        return (len(self.hits) / total) if total else None

    @property
    def ok(self) -> bool:
        return self.error is None and not self.misses and not self.false_positives


def _fetch_git_case(case: dict) -> Path:
    """Shallow-fetch one commit of a real repo into the cache, keyed by (repo, commit). Reused across runs."""
    CACHE.mkdir(parents=True, exist_ok=True)
    slug = case["repo"].rstrip("/").rsplit("/", 1)[-1].removesuffix(".git")
    dest = CACHE / f"{slug}-{case['commit'][:12]}"
    if (dest / ".git").exists():
        return dest
    dest.mkdir(parents=True, exist_ok=True)
    subprocess.run(["git", "init", "-q"], cwd=dest, check=True)
    subprocess.run(["git", "remote", "add", "origin", case["repo"]], cwd=dest, check=True)
    subprocess.run(["git", "fetch", "--depth", "1", "-q", "origin", case["commit"]], cwd=dest, check=True, timeout=300)
    subprocess.run(["git", "checkout", "-q", "FETCH_HEAD"], cwd=dest, check=True)
    return dest


def _run_map(repo: Path) -> dict:
    out = repo / ".audit-eval"
    r = subprocess.run([sys.executable, str(CLI), "map", str(repo), "--out", str(out)],
                       capture_output=True, text=True, timeout=600)
    if r.returncode != 0:
        raise RuntimeError(f"map failed (exit {r.returncode}): {(r.stderr or r.stdout).strip()[-2000:]}")
    return json.loads((out / "map.json").read_text(encoding="utf8"))


def _match(finding: dict, want: dict) -> bool:
    if finding["rule"] != want["rule"] or finding["function"] != want["function"]:
        return False
    if not finding["file"].replace("\\", "/").endswith(want["file"].replace("\\", "/")):
        return False
    return "line" not in want or finding["line"] == want["line"]


def run_case(case: dict) -> CaseResult:
    res = CaseResult(case["id"], case.get("lang", "?"))
    try:
        repo = ROOT / case["path"] if case["kind"] == "local" else _fetch_git_case(case)
        data = _run_map(repo)
    except Exception as e:  # a broken case must not stop the rest of the corpus
        res.error = str(e)
        return res
    findings = data["findings"]
    for want in case.get("expected", []):
        hit = next((f for f in findings if _match(f, want)), None)
        (res.hits if hit else res.misses).append(want)
    for bad in case.get("must_be_absent", []):
        hit = next((f for f in findings if f["rule"] == bad["rule"] and f["function"] == bad["function"]), None)
        if hit:
            res.false_positives.append(bad)
    return res


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--corpus", type=Path, default=Path(__file__).resolve().parent / "corpus.yaml")
    ap.add_argument("--case", action="append", help="run only these case ids (repeatable)")
    ap.add_argument("--json", type=Path, help="also write a machine-readable summary here")
    args = ap.parse_args()

    cases = yaml.safe_load(args.corpus.read_text(encoding="utf8"))["cases"]
    if args.case:
        wanted = set(args.case)
        cases = [c for c in cases if c["id"] in wanted]
        missing = wanted - {c["id"] for c in cases}
        if missing:
            print(f"eval: no such case(s): {', '.join(sorted(missing))}", file=sys.stderr)
            return 2

    results = [run_case(c) for c in cases]

    by_rule: dict[str, list[int]] = {}  # rule -> [hits, total]
    for r in results:
        for want in r.hits + r.misses:
            slot = by_rule.setdefault(want["rule"], [0, 0])
            slot[1] += 1
            if want in r.hits:
                slot[0] += 1

    print(f"{len(results)} case(s)\n")
    for r in results:
        if r.error:
            print(f"  ERROR  {r.case_id} ({r.lang}): {r.error}")
            continue
        status = "ok" if r.ok else "FAIL"
        print(f"  {status:5} {r.case_id} ({r.lang}): {len(r.hits)}/{len(r.hits) + len(r.misses)} found"
              + (f", {len(r.false_positives)} false positive(s)" if r.false_positives else ""))
        for m in r.misses:
            print(f"          missed: {m['rule']} @ {m['function']} ({m['file']})")
        for fp in r.false_positives:
            print(f"          should be absent but fired: {fp['rule']} @ {fp['function']}")

    print("\nRecall by rule:")
    for rule, (hit, total) in sorted(by_rule.items()):
        print(f"  {rule:<24} {hit}/{total}")

    errored = [r for r in results if r.error]
    passed = sum(r.ok for r in results if not r.error)
    print(f"\n{passed}/{len(results) - len(errored)} case(s) fully passed"
         + (f", {len(errored)} errored" if errored else ""))

    if args.json:
        summary = {"cases": [{"id": r.case_id, "lang": r.lang, "recall": r.recall, "ok": r.ok,
                              "hits": len(r.hits), "misses": r.misses,
                              "false_positives": r.false_positives, "error": r.error} for r in results],
                  "by_rule": {k: {"hits": v[0], "total": v[1]} for k, v in by_rule.items()}}
        args.json.write_text(json.dumps(summary, indent=2), encoding="utf8")
        print(f"wrote {args.json}")

    return 0 if all(r.ok for r in results) else 1


if __name__ == "__main__":
    sys.exit(main())
