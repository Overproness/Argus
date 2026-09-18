"""Run a command with tracers attached via environment variables.

Python processes pick the tracer up through a `sitecustomize` on PYTHONPATH,
so scripts, `-m pytest`, servers and their Python subprocesses are all traced.
Other languages read the same AUDIT_TRACE_* variables from their own adapters.
"""
from __future__ import annotations

import os
import subprocess
from pathlib import Path

SCRIPTS_DIR = Path(__file__).resolve().parents[2]
BOOT_DIR = Path(__file__).resolve().parent / "py" / "boot"


def trace_env(repo: Path, trace_dir: Path, stall_ms: float, shapes: bool) -> dict[str, str]:
    env = dict(os.environ)
    env["AUDIT_TRACE_ROOT"] = str(repo)
    env["AUDIT_TRACE_DIR"] = str(trace_dir)
    env["AUDIT_TRACE_STALL_MS"] = str(stall_ms)
    env["AUDIT_TRACE_SHAPES"] = "1" if shapes else "0"
    prev = env.get("PYTHONPATH")
    env["PYTHONPATH"] = os.pathsep.join([str(BOOT_DIR), str(SCRIPTS_DIR)] + ([prev] if prev else []))
    return env


def run(repo: Path, trace_dir: Path, cmd: list[str], stall_ms: float, shapes: bool) -> int:
    trace_dir.mkdir(parents=True, exist_ok=True)
    env = trace_env(repo, trace_dir, stall_ms, shapes)
    return subprocess.call(cmd, cwd=repo, env=env)
