"""Chrome trace-event JSON (Rust `tracing-chrome`, or any writer of the format) as runtime evidence.

`tracing-chrome` emits a `B`/`E` pair each time a span is entered and exited, so an async fn's span gives one
slice per poll. A slice with long self time is a poll that held its thread: a synchronous call blocking the
async runtime. `X` (complete) events are read too; async `b`/`e` pairs are ignored.

Slices map to repo functions by their `file`/`line` args when the writer records them (an `#[instrument]`
attribute sits a few lines above the fn), else by span name. A slice that maps to nothing is transparent: its
time is self time of the nearest mapped ancestor, as library calls are in the Python tracer.

Limits: Threaded-style spans carry no id, so each poll is its own call (a callee's per-poll calls can overstate
N+1 fan-out); a slice is a stall only with an async function on its stack, since a long synchronous slice on a
plain thread blocks nothing.
"""
from __future__ import annotations

import json
from pathlib import Path

from .spans import FnRef, Mapper
from .store import CallRec, StallRec, Trace

PID_BASE = 3_000_000_000
FILE_KEYS = ("file", "filename", "code.filepath", "code.file")
LINE_KEYS = ("line", "lineno", "code.lineno", "code.line")
ATTR_SLACK = 8  # lines an attribute may sit above its fn


def _arg(args: dict, keys: tuple[str, ...]):
    return next((args[k] for k in keys if k in args), None)


def _threads(events: list[dict]):
    """Yield (pid, tid, [('B'|'E', event)]) with each thread's events well nested."""
    per: dict[tuple, list[dict]] = {}
    for e in events:
        if isinstance(e, dict) and e.get("ph") in ("B", "E", "X") and "ts" in e:
            per.setdefault((e.get("pid", 0), e.get("tid", 0)), []).append(e)
    for (pid, tid), evs in per.items():
        if any(e["ph"] == "X" for e in evs):
            seq = []
            for e in evs:
                if e["ph"] == "X":
                    seq += [("B", e), ("E", {**e, "ts": e["ts"] + e.get("dur", 0), "_begin": e["ts"]})]
                else:
                    seq.append((e["ph"], e))
            # at equal times: ends before begins, inner ends first, longer spans begin first
            seq.sort(key=lambda t: (t[1]["ts"], 0 if t[0] == "E" else 1,
                                    -t[1]["_begin"] if t[0] == "E" else -t[1].get("dur", 0)))
        else:
            seq = sorted(((e["ph"], e) for e in evs), key=lambda t: t[1]["ts"])  # stable: file order at ties
        yield pid, tid, seq


class _Resolver:
    def __init__(self, mapper: Mapper, map_data: dict):
        self.m = mapper
        self.is_async = {tuple(f["id"].rsplit(":", 2)[:2]): f["is_async"] for f in map_data["functions"]}
        self.cache: dict[tuple, FnRef | None] = {}

    def __call__(self, name: str, file, line) -> FnRef | None:
        key = (name, file, line)
        if key not in self.cache:
            self.cache[key] = self._find(name, file, line)
        return self.cache[key]

    def _find(self, name, file, line) -> FnRef | None:
        if file and line:
            for k in range(ATTR_SLACK + 1):
                hit = self.m.by_location(str(file), line + k)
                if hit is not None and (k == 0 or hit.start >= line):
                    return hit
        return self.m.by_name(name)

    def async_(self, fn: FnRef) -> bool:
        return bool(self.is_async.get((fn.key[0], str(fn.key[1]))))


def _read(path: Path) -> list[dict]:
    try:
        doc = json.loads(path.read_text(encoding="utf8"))
    except (OSError, json.JSONDecodeError):
        # tracing-chrome leaves the array unterminated when the process is killed
        try:
            doc = json.loads(path.read_text(encoding="utf8").rstrip().rstrip(",") + "]")
        except (OSError, json.JSONDecodeError):
            return []
    return doc.get("traceEvents", []) if isinstance(doc, dict) else doc if isinstance(doc, list) else []


def attach(trace: Trace, trace_dir: Path, map_data: dict, repo: Path, stall_s: float = 0.1) -> Trace:
    files = sorted((trace_dir / "chrome").glob("*.json"))
    if not files:
        return trace
    resolve = _Resolver(Mapper(map_data, repo), map_data)
    runs, calls, stalls = list(trace.runs), list(trace.calls), list(trace.stalls)
    for idx, f in enumerate(files):
        pid, seq_id, got = PID_BASE + idx, 0, 0
        for _, tid, seq in _threads(_read(f)):
            stack: list[dict] = []
            for kind, e in seq:
                if kind == "B":
                    args = e.get("args") or {}
                    ln = _arg(args, LINE_KEYS)
                    fn = resolve(str(e.get("name", "")), _arg(args, FILE_KEYS), int(ln) if str(ln).isdigit() else None)
                    if fn is not None:
                        seq_id += 1
                    up = next((s for s in reversed(stack) if s["fn"]), None)
                    stack.append({"fn": fn, "start": e["ts"] / 1e6, "child": 0.0, "id": seq_id if fn else 0,
                                  "parent": up["id"] if up else 0})
                elif stack:
                    s = stack.pop()
                    if s["fn"] is None:
                        continue
                    dur = max(e["ts"] / 1e6 - s["start"], 0.0)
                    self_s = max(dur - s["child"], 0.0)
                    up = next((a for a in reversed(stack) if a["fn"]), None)
                    if up:
                        up["child"] += dur
                    fn, got = s["fn"], got + 1
                    tid_i = tid if isinstance(tid, int) else 0
                    calls.append(CallRec(pid, s["id"], s["parent"], tid_i, fn.key[0], fn.key[1], fn.qualname,
                                         resolve.async_(fn), s["start"], dur, self_s, 1, self_s, {}))
                    frames = [x["fn"] for x in stack if x["fn"]] + [fn]
                    if self_s >= stall_s and any(resolve.async_(x) for x in frames):
                        stalls.append(StallRec(pid, s["id"], fn.key[0], fn.key[1], fn.qualname, self_s, s["start"],
                                               [(x.key[0], x.key[1], x.qualname) for x in frames]))
        runs.append({"pid": str(pid), "lang": "rust", "source": "chrome-trace", "argv": json.dumps([f.name]),
                     "stall_threshold": str(stall_s), "activations": str(got)})
    return Trace(runs, calls, stalls, list(trace.io), list(trace.counts))
