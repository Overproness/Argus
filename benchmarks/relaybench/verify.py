#!/usr/bin/env python3
"""Validate every contract independently of Argus: buggy fails, fixed passes."""
import argparse
import hashlib
import json
import os
import shutil
import subprocess
import sys
from pathlib import Path

BASE = Path(__file__).resolve().parent
sys.path.insert(0, str(BASE))
from build import source_hash


def check_integrity(root, sample):
    digest, files = source_hash(root / sample["path"])
    if digest != sample["sha256"] or files != sample["files"]:
        raise ValueError(f"Source changed since creation: {sample['id']}")


def run_oracle(bundle, sample, case, offline=True):
    repo = bundle / sample["path"]
    check_integrity(bundle, sample)
    if case["language"] == "python":
        command = [sys.executable, "-I", str(BASE / "judge/python_oracle.py"), "--repo", str(repo), "--scenario", case["oracle"]]
    else:
        if not shutil.which("cargo"):
            return dict(exit_code=2, error="cargo unavailable")
        project = bundle / "judge/native" / sample["id"]
        (project / "src").mkdir(parents=True, exist_ok=True)
        path = (repo / "edge").as_posix()
        (project / "Cargo.toml").write_text('[package]\nname = "relaybench_probe"\nversion = "0.1.0"\nedition = "2021"\n'
                                             f'[dependencies]\nedge = {{ path = {json.dumps(path)} }}\n'
                                             'tokio = { version = "=1.53.1", features = ["rt", "macros", "time", "sync"] }\n', encoding="utf-8")
        shutil.copyfile(BASE / "judge/rust_probe.rs", project / "src/main.rs")
        lock = (repo / "edge/Cargo.lock").read_text(encoding="utf-8")
        lock += '\n[[package]]\nname = "relaybench_probe"\nversion = "0.1.0"\ndependencies = ["edge", "tokio"]\n'
        (project / "Cargo.lock").write_text(lock, encoding="utf-8")
        command = ["cargo", "run", "--quiet", "--locked", "--manifest-path", str(project / "Cargo.toml")]
        if offline:
            command.append("--offline")
        command.extend(["--", case["oracle"]])
    env = dict(os.environ, CARGO_TARGET_DIR=str(bundle / "judge/cargo-target"))
    for key in ("HTTP_PROXY", "HTTPS_PROXY", "ALL_PROXY", "http_proxy", "https_proxy", "all_proxy"):
        env.pop(key, None)
    env["NO_PROXY"] = env["no_proxy"] = "127.0.0.1,localhost,::1"
    # Do not inherit credentials in a child that exercises faulting code.
    for key in list(env):
        if any(marker in key.upper() for marker in ("API_KEY", "AUTH_TOKEN", "SECRET", "PASSWORD")):
            env.pop(key)
    try:
        result = subprocess.run(command, cwd=repo, env=env, capture_output=True, text=True, encoding="utf-8", timeout=180 if case["language"] == "rust" else 8)
    except (subprocess.TimeoutExpired, OSError) as exc:
        return dict(exit_code=2, error=str(exc))
    try:
        observation = json.loads(result.stdout.strip().splitlines()[-1])
    except (ValueError, IndexError):
        return dict(exit_code=2, error=(result.stderr or result.stdout)[-2500:])
    if result.returncode not in (0, 1) or "error" in observation:
        return dict(exit_code=2, **observation)
    # Rust returns successful execution with a boolean contract result. Python
    # uses 0/1 directly. In neither case does a compilation error prove a bug.
    return dict(exit_code=0 if observation["satisfied"] else 1, **observation)


def verify(bundle, language=None, case_ids=None, offline=True):
    manifest = json.loads((bundle / "judge/manifest.json").read_text(encoding="utf-8"))
    for name, expected in manifest.get("oracle_sha256", {}).items():
        actual = hashlib.sha256((BASE / "judge" / name).read_bytes()).hexdigest()
        if actual != expected:
            raise ValueError(f"Oracle changed since creation: {name}; create a new bundle")
    samples = {s["id"]: s for s in manifest["samples"]}
    cases = [c for c in manifest["cases"] if (not language or c["language"] == language)
             and (not case_ids or c["id"] in case_ids)]
    if not cases or (case_ids and set(case_ids) - {c["id"] for c in cases}):
        raise ValueError("Unknown or empty case selection")
    rows = []
    for case in cases:
        outcomes = {}
        for side in ("buggy", "fixed"):
            outcomes[side] = run_oracle(bundle, samples[case[side + "_sample"]], case, offline)
        row = dict(case=case["id"], title=case["title"], **outcomes)
        row["ok"] = outcomes["buggy"]["exit_code"] == 1 and outcomes["fixed"]["exit_code"] == 0
        rows.append(row)
        print(f"{case['id']}: {'PASS' if row['ok'] else 'FAIL'} (buggy={outcomes['buggy']['exit_code']}, fixed={outcomes['fixed']['exit_code']})", flush=True)
        if not row["ok"]:
            print(json.dumps(outcomes, indent=2), flush=True)
    result = dict(schema=1, complete=len(cases) == len(manifest["cases"]),
                  oracle_passes=sum(row["ok"] for row in rows), cases=len(rows), results=rows)
    (bundle / "judge/verification.json").write_text(json.dumps(result, indent=2), encoding="utf-8")
    return result


def main():
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--bundle", type=Path, required=True)
    ap.add_argument("--language", choices=["python", "rust"])
    ap.add_argument("--case", action="append")
    ap.add_argument("--allow-downloads", action="store_true", help="allow cargo to fetch missing dependencies; default offline")
    args = ap.parse_args()
    result = verify(args.bundle.resolve(), args.language, args.case, not args.allow_downloads)
    return 0 if result["oracle_passes"] == result["cases"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
