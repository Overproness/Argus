"""Linter results (SARIF) as extra evidence for map findings.

Most linters can write SARIF, the standard result format. Each result is placed in the map function whose
lines contain it. A result whose rule means the same as a map finding on that function (ruff `ASYNC210` and
`blocking-in-async`, `noctx` and `io-without-timeout`, ...) corroborates the finding: an independent tool
reached the same conclusion. The rest are listed as leads the map did not produce.
"""
from __future__ import annotations

import json
import re
import shutil
import subprocess
from dataclasses import asdict, dataclass
from pathlib import Path
from urllib.parse import unquote, urlparse

from .procs import resolve

# linter rule id -> the map rule it corroborates
EQUIVALENT: list[tuple[re.Pattern, str]] = [(re.compile(p, re.I), r) for p, r in [
    (r"^ASYNC[12]\d\d$|VSTHRD00[2]|VSTHRD103|blocking[-_]?(call|in[-_]async)|sync-?over-?async|"
     r"AsyncFixer0[24]|block[_-]?on|BlockingMethod|no-sync-in-async", "blocking-in-async"),
    (r"^S113$|^noctx$|missing[-_]?timeout|no[-_]?timeout|http[-_]without[-_]timeout|B113|G107|G114|"
     r"requests-without-timeout", "io-without-timeout"),
    (r"no-await-in-loop|^PERF203$|n\+?1|nplusone|await-in-loop|query-in-loop|db-in-loop", "io-in-loop"),
    (r"await[_-]holding[_-](lock|refcell)|lock-across-await|holding-lock", "lock-across-await"),
    (r"unbounded[-_]?(channel|queue)", "unbounded-channel"),
    (r"redos|regex.*(backtrack|catastroph)|unsafe-regex|safe-regex", "redos-regex"),
    (r"float[-_]?(money|currency)|float-equality|money", "float-money"),
    (r"unwrap[_-]used|expect[_-]used|unwrap-in-result|panic", "panic-on-external-data"),
]]
LEVELS = {"error": 3, "warning": 2, "note": 1, "none": 0}

# Linters Argus can start itself (only if installed). Others: run them and pass the SARIF to `lint-import`.
RUNNERS: dict[str, dict] = {
    "ruff": {"cmd": ["ruff", "check", "--output-format", "sarif", "--exit-zero", "."], "langs": ["python"]},
    "golangci-lint": {"cmd": ["golangci-lint", "run", "--out-format", "sarif", "./..."], "langs": ["go"]},
    "semgrep": {"cmd": ["semgrep", "scan", "--sarif", "--quiet", "--config", "{config}", "."], "langs": ["any"],
                "needs": "config"},
}


@dataclass
class LintResult:
    tool: str
    rule: str
    level: str
    message: str
    file: str
    line: int


def _uri_path(uri: str) -> str:
    if uri.startswith("file:"):
        p = unquote(urlparse(uri).path)
        return p[1:] if re.match(r"^/[A-Za-z]:", p) else p
    return unquote(uri)


def load_sarif(path: Path, repo: Path) -> list[LintResult]:
    doc = json.loads(path.read_text(encoding="utf8"))
    if not isinstance(doc, dict) or "runs" not in doc:
        raise ValueError(f"{path}: not a SARIF file (no `runs`)")
    root = repo.resolve().as_posix().rstrip("/") + "/"
    out: list[LintResult] = []
    for run in doc["runs"]:
        tool = (run.get("tool", {}).get("driver", {}) or {}).get("name", "?")
        rules = {r.get("id"): r for r in (run.get("tool", {}).get("driver", {}) or {}).get("rules", []) or []}
        for res in run.get("results", []) or []:
            rid = res.get("ruleId") or (res.get("rule") or {}).get("id") or "?"
            level = res.get("level") or (rules.get(rid, {}).get("defaultConfiguration") or {}).get("level") or "warning"
            msg = (res.get("message") or {}).get("text", "")
            for loc in res.get("locations", []) or [{}]:
                phys = loc.get("physicalLocation") or {}
                uri = (phys.get("artifactLocation") or {}).get("uri")
                line = (phys.get("region") or {}).get("startLine")
                if not uri or not line:
                    continue
                p = _uri_path(uri).replace("\\", "/")
                if p.startswith(root):
                    p = p[len(root):]
                out.append(LintResult(tool, rid, level, msg.split("\n")[0][:300], p.lstrip("./") if p.startswith("./") else p, int(line)))
    return out


def correlate(map_data: dict, results: list[LintResult]) -> dict:
    by_file: dict[str, list[dict]] = {}
    for f in map_data["functions"]:
        file, _, _ = f["id"].rsplit(":", 2)
        by_file.setdefault(file, []).append(f)

    def owner(r: LintResult):
        fns = [f for f in by_file.get(r.file, []) if f["lines"][0] <= r.line <= f["lines"][1]]
        return min(fns, key=lambda f: f["lines"][1] - f["lines"][0]) if fns else None

    findings: dict[tuple, dict] = {}
    for f in map_data["findings"]:
        findings.setdefault((f["function"], f["rule"]), f)
    corroborated: dict[tuple, list[LintResult]] = {}
    leads: list[dict] = []
    for r in results:
        fn = owner(r)
        rule = next((mr for rx, mr in EQUIVALENT if rx.search(r.rule)), None)
        key = (fn["qualname"], rule) if fn and rule else None
        if key in findings:
            corroborated.setdefault(key, []).append(r)
        else:
            leads.append({**asdict(r), "function": fn["qualname"] if fn else None, "maps_to": rule})
    leads.sort(key=lambda x: -LEVELS.get(x["level"], 1))
    return {
        "corroborated": [{"function": k[0], "rule": k[1], "location": f"{findings[k]['file']}:{findings[k]['line']}",
                          "linters": sorted({f"{r.tool}:{r.rule}" for r in rs}),
                          "results": [asdict(r) for r in rs][:5]} for k, rs in corroborated.items()],
        "leads": leads,
        "totals": {"results": len(results), "corroborated_findings": len(corroborated), "leads": len(leads)},
    }


def to_markdown(d: dict) -> str:
    out = ["# Linter evidence\n", f"{d['totals']['results']} result(s): {d['totals']['corroborated_findings']} "
           f"map finding(s) corroborated, {d['totals']['leads']} other lead(s).\n", "## Corroborated map findings\n"]
    out += [f"- `{c['function']}` **{c['rule']}** at `{c['location']}`: {', '.join(c['linters'])}" for c in d["corroborated"]] \
        or ["None.\n"]
    out.append("\n## Leads the map did not produce\n")
    out += [f"- [{x['level']}] `{x['tool']}:{x['rule']}` at `{x['file']}:{x['line']}`"
            f"{' in `' + x['function'] + '`' if x['function'] else ''}: {x['message']}" for x in d["leads"][:60]] or ["None.\n"]
    return "\n".join(out) + "\n"


def run_linters(repo: Path, out_dir: Path, names: list[str] | None = None, config: str | None = None) -> list[tuple[str, str]]:
    """Run the installed linters from RUNNERS, saving SARIF under out_dir/lint/. -> [(name, status)]"""
    (out_dir / "lint").mkdir(parents=True, exist_ok=True)
    log = []
    for name, spec in RUNNERS.items():
        if names and name not in names:
            continue
        if shutil.which(spec["cmd"][0]) is None:
            log.append((name, "not installed"))
            continue
        if spec.get("needs") == "config" and not config:
            log.append((name, "needs --semgrep-config"))
            continue
        cmd = [c.replace("{config}", config or "") for c in spec["cmd"]]
        p = subprocess.run(resolve(cmd), cwd=repo, capture_output=True, text=True, timeout=900)
        if not p.stdout.strip().startswith("{"):
            log.append((name, f"no SARIF produced (exit {p.returncode}): {p.stderr.strip()[:200]}"))
            continue
        (out_dir / "lint" / f"{name}.sarif").write_text(p.stdout, encoding="utf8")
        log.append((name, "ok"))
    return log


def write(repo: Path, out_dir: Path, sarif_files: list[Path]) -> tuple[Path, Path, dict]:
    map_data = json.loads((out_dir / "map.json").read_text(encoding="utf8"))
    results = [r for f in sarif_files for r in load_sarif(f, repo)]
    data = correlate(map_data, results)
    jp, mp = out_dir / "lint.json", out_dir / "lint.md"
    jp.write_text(json.dumps(data, indent=2), encoding="utf8")
    mp.write_text(to_markdown(data), encoding="utf8")
    return jp, mp, data
