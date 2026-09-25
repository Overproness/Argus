"""Run reproduction tests and collect their outcomes and evidence.

Reproductions live in `<out>/repros/test_*.py`. Each is a pytest file, whatever
the audited language: Python code is exercised in-process, other languages
through `native` (black-box runs and native probes) against a `faults`
server. The helpers, and any program they run, print `@@evidence {...}` lines,
which pytest captures per test into the JUnit XML this runner parses. A test that *fails* when the predicted
effect appears is the wrong shape: write assertions so that **passing means
the finding is reproduced**, and use `evidence(...)` for the numbers.

Isolation: every file runs in its own pytest process, from the repo root, so one
reproduction's monkeypatches, `chdir`, imported modules or dead servers cannot
leak into the next. Inside a file the `isolation` plugin restores the working
directory, `sys.path` and socket patches after each test.

Results are kept per file under `<out>/repros/.results/` and `repro.json` is
rebuilt from them under a lock, so investigators running `repro --file` in
parallel never overwrite each other's results.

Interpreter: the repo's own environment when there is one (`<out>/venv` from
`repro-env`, then `<repo>/.venv`, `<repo>/venv`), so the code under test imports
with its real dependencies. `--python` overrides.
"""
from __future__ import annotations

import json
import os
import shutil
import subprocess
import sys
import tempfile
import venv
import xml.etree.ElementTree as ET
from contextlib import contextmanager
from datetime import datetime, timezone
from pathlib import Path

from .harness import EVIDENCE_PREFIX

SCRIPTS_DIR = Path(__file__).resolve().parents[2]
PER_FILE_TIMEOUT = 300
BIN = "Scripts" if os.name == "nt" else "bin"
EXE = "python.exe" if os.name == "nt" else "python"


# --- interpreter -------------------------------------------------------------------------------

def _venv_python(d: Path) -> Path:
    return d / BIN / EXE


def _has_pytest(py: str) -> bool:
    try:
        r = subprocess.run([py, "-c", "import pytest"], capture_output=True, timeout=60)
    except (OSError, subprocess.TimeoutExpired):
        return False
    return r.returncode == 0


def pick_python(repo: Path, out_dir: Path, python: str | None = None) -> tuple[str, str]:
    """-> (interpreter, why). The repo's environment when it has one with pytest, else Argus's own."""
    if python:
        return python, "--python"
    if os.environ.get("ARGUS_REPRO_PYTHON"):
        return os.environ["ARGUS_REPRO_PYTHON"], "ARGUS_REPRO_PYTHON"
    for d in (out_dir / "venv", repo / ".venv", repo / "venv"):
        py = _venv_python(d)
        if py.exists() and _has_pytest(str(py)):
            return str(py), f"repo environment {d}"
    return sys.executable, "argus interpreter (no repo environment; run `repro-env` if imports fail)"


def setup_env(repo: Path, out_dir: Path, base_python: str | None = None, extra: list[str] | None = None) -> dict:
    """Create `<out>/venv` with the repo's dependencies plus pytest, for reproductions that import the repo."""
    target = out_dir / "venv"
    base = base_python or shutil.which("python3") or sys.executable
    log: list[str] = []
    if not _venv_python(target).exists():
        if base == sys.executable:
            venv.EnvBuilder(with_pip=True, clear=False).create(target)
        else:
            r = subprocess.run([base, "-m", "venv", str(target)], capture_output=True, text=True)
            if r.returncode != 0:
                return {"ok": False, "python": None, "log": [r.stderr[-2000:]]}
    py = str(_venv_python(target))
    installs: list[list[str]] = []
    for req in ("requirements.txt", "requirements-dev.txt", "requirements/dev.txt", "requirements/base.txt"):
        if (repo / req).exists():
            installs.append(["-r", str(repo / req)])
    if not installs and any((repo / f).exists() for f in ("pyproject.toml", "setup.py", "setup.cfg")):
        installs.append(["-e", str(repo)])
    installs.append(["pytest", *(extra or [])])
    ok = True
    for args in installs:
        r = subprocess.run([py, "-m", "pip", "install", "-q", "--disable-pip-version-check", *args],
                           capture_output=True, text=True)
        log.append(f"pip install {' '.join(args)}: {'ok' if r.returncode == 0 else 'FAILED'}")
        if r.returncode != 0:
            ok = False
            log.append(r.stderr[-1500:])
    return {"ok": ok, "python": py, "log": log}


# --- running ---------------------------------------------------------------------------------

def _env(repo: Path) -> dict:
    env = dict(os.environ)
    env["PYTHONPATH"] = os.pathsep.join([str(SCRIPTS_DIR), str(repo)] + (
        [env["PYTHONPATH"]] if env.get("PYTHONPATH") else []))
    env["AUDIT_REPRO"] = "1"
    env["ARGUS_REPO"] = str(repo)  # harness.repo_root(): where tests and probes find the code
    env.pop("PYTHONHOME", None)
    return env


def run_pytest(repo: Path, files: list[Path], junit: Path, timeout: int, python: str | None = None) -> tuple[int, str]:
    cmd = [python or sys.executable, "-m", "pytest", "-q", "-p", "no:cacheprovider", "-p", "auditor.repro.isolation",
           "--tb=short", "-o", "addopts=", "-o", "junit_logging=system-out", f"--junitxml={junit}",
           "--rootdir", str(files[0].parent), *map(str, files)]
    try:
        r = subprocess.run(cmd, cwd=repo, env=_env(repo), capture_output=True, text=True, timeout=timeout)
    except subprocess.TimeoutExpired as e:
        out = e.stdout.decode("utf8", "replace") if isinstance(e.stdout, bytes) else (e.stdout or "")
        return 124, f"timed out after {timeout}s\n{out[-2000:]}"
    return r.returncode, (r.stdout + r.stderr)[-4000:]


def parse_junit(path: Path, file_name: str | None = None) -> list[dict]:
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
            "file": file_name or tc.get("classname", "").split(".")[-1] + ".py",
            "test": tc.get("name"), "outcome": outcome, "time_s": float(tc.get("time") or 0),
            "message": message, "evidence": ev,
            "finding": next((e["finding"] for e in ev if "finding" in e), None),
        })
    return out


def run_file(repo: Path, f: Path, timeout: int, python: str) -> dict:
    """One reproduction file in its own pytest process: {file, exit_code, output_tail, tests, ran_at}."""
    with tempfile.TemporaryDirectory(prefix="argus-junit-") as tmp:
        junit = Path(tmp) / "results.xml"
        rc, tail = run_pytest(repo, [f], junit, timeout, python)
        tests = parse_junit(junit, f.name)
    if not tests and rc not in (0, 5):
        # Collection error, import error or timeout: no testcase to attach it to, so make one.
        tests = [{"file": f.name, "test": f.stem, "outcome": "error", "time_s": 0.0,
                  "message": tail[-800:], "evidence": [], "finding": None}]
    return {"file": f.name, "exit_code": rc, "output_tail": tail if rc not in (0, 1) else "",
            "tests": tests, "ran_at": now(), "python": python}


def now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


@contextmanager
def _locked(path: Path):
    path.parent.mkdir(parents=True, exist_ok=True)
    with open(path, "a+") as fh:
        try:
            import fcntl
            fcntl.flock(fh, fcntl.LOCK_EX)
        except ImportError:  # Windows: best effort
            pass
        try:
            yield
        finally:
            try:
                import fcntl
                fcntl.flock(fh, fcntl.LOCK_UN)
            except ImportError:
                pass


def _atomic_write(path: Path, text: str):
    tmp = path.with_name(f".{path.name}.{os.getpid()}.tmp")
    tmp.write_text(text, encoding="utf8")
    os.replace(tmp, path)


def results_dir(out_dir: Path) -> Path:
    return out_dir / "repros" / ".results"


def aggregate(out_dir: Path, python: str | None = None, why: str | None = None) -> dict:
    """repro.json from the latest per-file results; files that no longer exist are dropped."""
    repro_dir = out_dir / "repros"
    tests, files, rcs, tails, ran = [], [], [], [], {}
    for rp in sorted(results_dir(out_dir).glob("*.json")):
        try:
            r = json.loads(rp.read_text(encoding="utf8"))
        except (OSError, json.JSONDecodeError):
            continue
        if not (repro_dir / r["file"]).exists():
            rp.unlink(missing_ok=True)
            continue
        files.append(str(repro_dir / r["file"]))
        tests += r["tests"]
        rcs.append(r["exit_code"])
        ran[r["file"]] = r["ran_at"]
        if r["output_tail"]:
            tails.append(f"--- {r['file']} (exit {r['exit_code']})\n{r['output_tail']}")
        python = python or r.get("python")
    bad = [c for c in rcs if c not in (0, 1, 5)]
    return {
        "meta": {"tool": "argus/repro", "generated_at": now(), "files": files, "ran_at": ran,
                 "python": python, "python_why": why, "isolation": "one process per file"},
        "tests": tests, "exit_code": bad[0] if bad else (1 if 1 in rcs else 0),
        "output_tail": "\n".join(tails)[-4000:],
    }


def run(repo: Path, out_dir: Path, target: Path | None = None, timeout: int = 600,
        python: str | None = None) -> dict:
    """Run every reproduction (or `target`), each file in its own process; returns the aggregate."""
    repo = repo.resolve()
    repro_dir = (out_dir / "repros").resolve()
    repro_dir.mkdir(parents=True, exist_ok=True)
    if target is not None:
        target = target.resolve()
        if target.parent != repro_dir:
            raise ValueError(f"{target} is not in {repro_dir}. Reproductions must live directly in this repo's "
                             f".audit/repros/; check that the repo path is exactly {str(repo)!r}.")
        if not target.exists():
            raise FileNotFoundError(target)
        files = [target]
    else:
        files = sorted(repro_dir.glob("test_*.py"))
    py, why = pick_python(repo, out_dir, python)
    if not files:
        res = aggregate(out_dir, py, why)
        res["output_tail"] = res["output_tail"] or f"no test_*.py under {repro_dir}"
        return res
    rdir = results_dir(out_dir)
    rdir.mkdir(parents=True, exist_ok=True)
    per_file = max(30, min(PER_FILE_TIMEOUT, timeout))
    deadline = datetime.now().timestamp() + timeout
    for f in files:
        left = int(deadline - datetime.now().timestamp())
        if left <= 0:
            r = {"file": f.name, "exit_code": 124, "output_tail": f"not run: the {timeout}s budget ran out",
                 "tests": [{"file": f.name, "test": f.stem, "outcome": "error", "time_s": 0.0,
                            "message": "not run: repro timeout budget exhausted", "evidence": [], "finding": None}],
                 "ran_at": now(), "python": py}
        else:
            r = run_file(repo, f, min(per_file, left), py)
        _atomic_write(rdir / f"{f.stem}.json", json.dumps(r, indent=2))
    with _locked(rdir / ".lock"):
        res = aggregate(out_dir, py, why)
        write(res, out_dir)
    return res


def status_of(d: dict | None, finding: str, repro_file: str | None = None) -> str:
    """passed / failed / missing for one finding: its own tests (by evidence id, else by file name)."""
    tests = [t for t in (d or {}).get("tests", []) if t.get("finding") == finding]
    if not tests and repro_file:
        name = Path(repro_file).name
        tests = [t for t in (d or {}).get("tests", []) if t.get("file") == name]
    if not tests:
        return "missing"
    return "passed" if any(t["outcome"] == "passed" for t in tests) else "failed"


def to_markdown(d: dict) -> str:
    out = ["# Reproduction results\n"]
    tests = d["tests"]
    counts = {}
    for t in tests:
        counts[t["outcome"]] = counts.get(t["outcome"], 0) + 1
    out.append(", ".join(f"{v} {k}" for k, v in sorted(counts.items())) or "no tests ran")
    out.append("\n_A passing test means the reproduction triggered the predicted effect. "
               "Evidence lines are what the harness measured. Each file ran in its own process"
               + (f" under `{d['meta'].get('python')}`" if d.get("meta", {}).get("python") else "") + "._\n")
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
    _atomic_write(jp, json.dumps(d, indent=2))
    _atomic_write(mp, to_markdown(d))
    return jp, mp
