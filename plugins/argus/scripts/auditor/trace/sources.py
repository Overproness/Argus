"""Everything recorded under .audit/trace/ as one Trace, whichever channel produced it, and imports of files
recorded elsewhere (CI, a staging box, another machine).

Runs accumulate: every `trace` and `trace-import` adds to the directory and the report covers them all.
Delete .audit/trace/ to start over.
"""
from __future__ import annotations

import json
import shutil
import time
from pathlib import Path

from . import chrome, otlp, profiles, spans, store
from .store import Trace


def collect(trace_dir: Path, map_data: dict, repo: Path, stall_s: float) -> Trace:
    """Python tracer databases, OTLP spans and heartbeats, then profiles and coverage. The order matters: a
    profile stall replaces a heartbeat gap it explains."""
    t = spans.attach(store.load(trace_dir), trace_dir, map_data, repo)
    t = profiles.attach(t, trace_dir, map_data, repo, stall_s)
    return chrome.attach(t, trace_dir, map_data, repo, stall_s)


def profile_kind(path: Path) -> str:
    """'cpuprofile' or 'speedscope', judged by content; ValueError for anything else."""
    try:
        doc = json.loads(path.read_text(encoding="utf8"))
    except (OSError, UnicodeDecodeError, json.JSONDecodeError) as e:
        raise ValueError(f"{path}: not a JSON profile ({e})") from None
    if isinstance(doc, dict) and "nodes" in doc and "samples" in doc:
        return "cpuprofile"
    if isinstance(doc, dict) and "profiles" in doc and ("shared" in doc or "speedscope" in str(doc.get("$schema"))):
        return "speedscope"
    raise ValueError(f"{path}: expected a V8 .cpuprofile or a speedscope JSON file "
                     "(py-spy --format speedscope --idle)")


def import_files(trace_dir: Path, otlp_files=(), profile_files=(), coverage=(), chrome_files=()) -> dict[str, int]:
    stamp = f"{time.time_ns() // 1_000_000}"
    trace_dir.mkdir(parents=True, exist_ok=True)
    kinds = [(Path(f), profile_kind(Path(f))) for f in profile_files]  # check them all before copying any
    n_spans = 0
    for i, f in enumerate(map(Path, otlp_files)):
        got = otlp.load_file(f)
        otlp.write_jsonl(got, trace_dir / f"import-{stamp}-{i}.spans.jsonl", {"argv": [f"imported from {f.name}"]})
        n_spans += len(got)
    for i, (f, kind) in enumerate(kinds):
        (trace_dir / "profiles").mkdir(exist_ok=True)
        shutil.copy(f, trace_dir / "profiles" / f"import-{stamp}-{i}.{kind}{'' if kind == 'cpuprofile' else '.json'}")
    for i, f in enumerate(map(Path, chrome_files)):
        (trace_dir / "chrome").mkdir(exist_ok=True)
        shutil.copy(f, trace_dir / "chrome" / f"import-{stamp}-{i}.json")
    n_cov = 0
    for i, f in enumerate(map(Path, coverage)):
        (trace_dir / "coverage").mkdir(exist_ok=True)
        for j, src in enumerate(sorted(f.glob("*.json")) if f.is_dir() else [f]):
            shutil.copy(src, trace_dir / "coverage" / f"import-{stamp}-{i}-{j}.json")
            n_cov += 1
    return {"spans": n_spans, "profiles": len(kinds), "coverage_files": n_cov, "chrome": len(chrome_files)}
