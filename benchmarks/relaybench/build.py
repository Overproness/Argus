#!/usr/bin/env python3
"""Create independent Git fix pairs and opaque, source-only audit targets."""
import argparse
import hashlib
import io
import json
import os
import random
import shutil
import subprocess
import sys
import zipfile
from pathlib import Path

BASE = Path(__file__).resolve().parent
ARGUS = BASE.parents[1]
sys.path.insert(0, str(BASE / "judge"))
from cases import CASES, CONTROLS

IGNORED_DIRS = {".git", ".audit", ".audit-eval", ".pytest_cache", ".venv", "target", "__pycache__"}


def git(repo, *args):
    env = dict(os.environ, GIT_AUTHOR_DATE="2026-01-01T00:00:00+00:00", GIT_COMMITTER_DATE="2026-01-01T00:00:00+00:00")
    result = subprocess.run(["git", "-c", "user.name=Relaybench", "-c", "user.email=relaybench@example.invalid", *args],
                            cwd=repo, env=env, capture_output=True, check=True)
    return result.stdout


def source_hash(root):
    hashes = {}
    for path in sorted(root.rglob("*")):
        if not path.is_file() or any(part in IGNORED_DIRS for part in path.relative_to(root).parts):
            continue
        hashes[path.relative_to(root).as_posix()] = hashlib.sha256(path.read_bytes()).hexdigest()
    return hashlib.sha256(json.dumps(hashes, sort_keys=True).encode()).hexdigest(), hashes


def mutate(repo, case):
    path = repo / case["file"]
    code = path.read_text(encoding="utf-8")
    if code.count(case["clean"]) != 1:
        raise ValueError(f"Seed {case['id']} does not match exactly once")
    path.write_text(code.replace(case["clean"], case["buggy"]), encoding="utf-8", newline="\n")


def export(repo, revision, target):
    archive = git(repo, "archive", "--format=zip", revision)
    target.mkdir(parents=True)
    with zipfile.ZipFile(io.BytesIO(archive)) as source:
        source.extractall(target)
    return source_hash(target)


def build(out, seed):
    if out.exists():
        raise FileExistsError(f"Refusing to replace existing bundle: {out}; choose a new --out")
    # Validate seeds before writing artifacts or running the detector.
    for case in CASES:
        source = (BASE / "baseline" / case["file"]).read_text(encoding="utf-8")
        if source.count(case["clean"]) != 1:
            raise ValueError(f"Invalid seed: {case['id']}")
    out.mkdir(parents=True)
    history = out / "judge/history"
    shutil.copytree(BASE / "baseline", history,
                    ignore=shutil.ignore_patterns(*IGNORED_DIRS, "data", "*.sqlite*", ".env", ".env.*"))
    git(history, "init", "-q")
    git(history, "add", ".")
    git(history, "commit", "-q", "-m", "Initial Relay service")
    clean = git(history, "rev-parse", "HEAD").decode().strip()
    jobs = [("clean", None, clean)]
    cases = []
    for case in CASES:
        git(history, "checkout", "-q", "--detach", clean)
        mutate(history, case)
        git(history, "add", case["file"])
        git(history, "commit", "-q", "-m", f"Revise {case['function'].split('.')[-1]} implementation")
        buggy = git(history, "rev-parse", "HEAD").decode().strip()
        git(history, "checkout", clean, "--", case["file"])
        git(history, "commit", "-q", "-m", f"Restore contract: {case['title']}")
        fixed = git(history, "rev-parse", "HEAD").decode().strip()
        git(history, "branch", f"pair/{case['id']}", fixed)
        assert git(history, "rev-parse", f"{fixed}^").decode().strip() == buggy
        row = {k: v for k, v in case.items() if k not in {"clean", "buggy"}}
        row.update(buggy_commit=buggy, fix_commit=fixed)
        cases.append(row)
        jobs.extend([("buggy", case["id"], buggy), ("fixed", case["id"], fixed)])
    git(history, "checkout", "-q", "--detach", clean)
    for case in CASES:
        mutate(history, case)
    git(history, "add", ".")
    git(history, "commit", "-q", "-m", "Integrate delivery and edge workload revisions")
    mixed = git(history, "rev-parse", "HEAD").decode().strip()
    git(history, "branch", "mixed", mixed)
    jobs.append(("mixed", None, mixed))
    random.Random(seed).shuffle(jobs)
    samples = []
    for i, (role, case_id, revision) in enumerate(jobs):
        name = f"sample-{i:03d}"
        target = out / "targets" / name
        digest, files = export(history, revision, target)
        samples.append(dict(id=name, role=role, case=case_id, commit=revision,
                            path=target.relative_to(out).as_posix(), sha256=digest, files=files))
        if case_id:
            next(c for c in cases if c["id"] == case_id)[role + "_sample"] = name
    detector_hash, _ = source_hash(ARGUS / "plugins/argus")
    revision = subprocess.check_output(["git", "-C", str(ARGUS), "rev-parse", "HEAD"], text=True).strip()
    oracle_hashes = {name: hashlib.sha256((BASE / "judge" / name).read_bytes()).hexdigest()
                     for name in ("cases.py", "python_oracle.py", "rust_probe.rs")}
    manifest = dict(schema=1, name="relaybench", split="synthetic-dev", seed=seed,
                    authorship="Created with knowledge of Argus. Not held-out or independent real-world evidence.",
                    argus_revision_at_creation=revision, argus_plugin_sha256_at_creation=detector_hash,
                    oracle_sha256=oracle_hashes, cases=cases, controls=CONTROLS, samples=samples)
    (out / "judge/manifest.json").write_text(json.dumps(manifest, indent=2), encoding="utf-8")
    (out / "README.txt").write_text("Evaluator bundle. Give an agent only one targets/sample-* directory.\n"
                                   "Keep judge/, history, manifests and oracle results outside its environment.\n", encoding="utf-8")
    return manifest


def main():
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--out", type=Path, default=ARGUS / ".audit/relaybench")
    ap.add_argument("--seed", type=int, default=20261014)
    args = ap.parse_args()
    manifest = build(args.out.resolve(), args.seed)
    print(f"Built {len(manifest['cases'])} pairs, {len(manifest['samples'])} source-only targets in {args.out.resolve()}")
    print("Role mapping and Git commits are in judge/manifest.json, for the evaluator only.")


if __name__ == "__main__":
    raise SystemExit(main())
