"""Run a command with tracers attached via environment variables.

Python processes pick the tracer up through a `sitecustomize` on PYTHONPATH,
so scripts, `-m pytest`, servers and their Python subprocesses are all traced.

Every other language is observed through two language-neutral channels:
  otlp       an OTLP/HTTP receiver; OTEL_EXPORTER_OTLP_* point any OpenTelemetry
             SDK or agent in the program at it (spans -> *.spans.jsonl)
  heartbeat  the program's own periodic output lines, timestamped as they
             arrive (-> *.heartbeat.jsonl); gaps are stalls
and, where the runtime has them built in, native function-level channels:
  node       V8 sampling profiles and exact call counts via NODE_OPTIONS and
             NODE_V8_COVERAGE (-> node-prof/, node-cov/); see profiles.py
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

from ..procs import resolve
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


NODE_COMMANDS = {"node", "npm", "npx", "yarn", "pnpm", "tsx", "ts-node"}


def is_node_command(cmd: list[str]) -> bool:
    return bool(cmd) and Path(cmd[0]).stem.lower() in NODE_COMMANDS


def node_env(env: dict, trace_dir: Path, run_id: str, cmd: list[str]) -> dict:
    """Sampling profile (--cpu-prof) and exact call counts (NODE_V8_COVERAGE) for every Node process started.
    Each run gets its own directories, so profiles keep their own command and clock when runs accumulate."""
    prof, cov = trace_dir / "node-prof" / run_id, trace_dir / "node-cov" / run_id
    prof.mkdir(parents=True, exist_ok=True)
    cov.mkdir(parents=True, exist_ok=True)
    flags = f'--cpu-prof --cpu-prof-dir="{prof.as_posix()}" --cpu-prof-interval=500'
    env["NODE_OPTIONS"] = f"{env.get('NODE_OPTIONS', '')} {flags}".strip()
    env["NODE_V8_COVERAGE"] = str(cov)
    # V8 stamps profiles with the clock perf_counter reads; this pair puts them on the epoch clock.
    meta = {"argv": cmd, "epoch_ns": time.time_ns(), "mono_ns": time.perf_counter_ns()}
    (prof / "_argus_meta.json").write_text(json.dumps(meta), encoding="utf8")
    return env


def run(repo: Path, trace_dir: Path, cmd: list[str], stall_ms: float, shapes: bool,
        otlp: bool = False, heartbeat: str | None = None, node: bool = False) -> int:
    trace_dir.mkdir(parents=True, exist_ok=True)
    env = trace_env(repo, trace_dir, stall_ms, shapes)
    stamp = f"{time.time_ns() // 1_000_000}-{os.getpid()}"  # one server process may trace twice in a second
    if node:
        env = node_env(env, trace_dir, stamp, cmd)
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
            rc = _run_teed(resolve(cmd), repo, env, trace_dir / f"{stamp}.heartbeat.jsonl", meta)
        else:
            rc = subprocess.call(resolve(cmd), cwd=repo, env=env)
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
