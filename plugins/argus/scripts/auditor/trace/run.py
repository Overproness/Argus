"""Run a command with tracers attached via environment variables.

Python processes pick the tracer up through a `sitecustomize` on PYTHONPATH,
so scripts, `-m pytest`, servers and their Python subprocesses are all traced.

Every other language is observed through two language-neutral channels:
  otlp       an OTLP/HTTP receiver; OTEL_EXPORTER_OTLP_* point any OpenTelemetry
             SDK or agent in the program at it (spans -> *.spans.jsonl)
  heartbeat  the program's own periodic output lines, timestamped as they
             arrive (-> *.heartbeat.jsonl); gaps are stalls
Native adapters for other languages read the same AUDIT_TRACE_* variables.
"""
from __future__ import annotations

import json
import os
import re
import subprocess
import sys
import threading
import time
from pathlib import Path

from . import otlp as otlp_mod

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


def _run_teed(cmd: list[str], cwd: Path, env: dict, log: Path, meta: dict) -> int:
    """Run cmd, echo its output, and record matching lines with their arrival time (ns since epoch)."""
    rx = re.compile(meta["regex"])
    proc = subprocess.Popen(cmd, cwd=cwd, env=env, stdout=subprocess.PIPE, stderr=subprocess.STDOUT,
                            stdin=subprocess.DEVNULL)
    with log.open("w", encoding="utf8") as fh:
        fh.write(json.dumps({"meta": meta}) + "\n")

        def pump():
            for raw in iter(proc.stdout.readline, b""):
                t = time.time_ns()
                line = raw.decode("utf8", "replace").rstrip("\r\n")
                sys.stdout.write(line + "\n")
                if rx.search(line):
                    fh.write(json.dumps({"t_ns": t, "line": line[:300]}) + "\n")

        reader = threading.Thread(target=pump, daemon=True)
        reader.start()
        rc = proc.wait()
        reader.join(5)
    sys.stdout.flush()
    return rc


def run(repo: Path, trace_dir: Path, cmd: list[str], stall_ms: float, shapes: bool,
        otlp: bool = False, heartbeat: str | None = None) -> int:
    trace_dir.mkdir(parents=True, exist_ok=True)
    env = trace_env(repo, trace_dir, stall_ms, shapes)
    stamp = f"{int(time.time())}-{os.getpid()}"
    receiver = None
    if otlp:
        receiver = otlp_mod.Receiver().start()
        for k, v in receiver.env(repo.name).items():
            if k == "OTEL_SERVICE_NAME" and k in env:
                continue  # keep the program's own service name
            env[k] = v
    try:
        if heartbeat:
            meta = {"regex": heartbeat, "argv": cmd, "stall_ms": stall_ms}
            rc = _run_teed(cmd, repo, env, trace_dir / f"{stamp}.heartbeat.jsonl", meta)
        else:
            rc = subprocess.call(cmd, cwd=repo, env=env)
    finally:
        if receiver is not None:
            time.sleep(0.5)  # batch exporters flush at exit; give the last request time to land
            receiver.stop()
            if receiver.spans:
                otlp_mod.write_jsonl(receiver.spans, trace_dir / f"{stamp}.spans.jsonl", {"argv": cmd})
            for err in receiver.errors[:5]:
                print(f"otlp: rejected a payload: {err}", file=sys.stderr)
            print(f"otlp: {len(receiver.spans)} span(s) in {receiver.requests} export(s)", file=sys.stderr)
    return rc
