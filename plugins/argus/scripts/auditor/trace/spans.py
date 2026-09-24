"""Language-neutral runtime evidence: OpenTelemetry spans and heartbeat gaps, in the trace store's shape.

Spans come from any instrumented program (see otlp.py). Each span is mapped to a
function of the static map:
  1. by code location (`code.file.path` + `code.line.number`, or the older
     `code.filepath` + `code.lineno`);
  2. by qualified name (`code.function.name`, or `code.namespace` + `code.function`);
  3. for internal spans, by the span name when it names a function (`Client.fetch`).
Client and producer spans (HTTP, DB, RPC, messaging) become IoRec rows: the
external calls a function made, with their durations and errors.

Heartbeats are a program's own periodic output lines (a tick log), timestamped
as they arrive. A gap much longer than the usual interval means the program was
unresponsive. The gap is attributed to the deepest span that covers it: when
that is a client span, the function that made the call. Without spans the stall
is recorded at program level.
"""
from __future__ import annotations

import json
import re
from collections import defaultdict
from dataclasses import dataclass
from pathlib import Path
from statistics import median

from . import otlp
from .store import CallRec, IoRec, StallRec, Trace

PID_BASE = 1_000_000_000  # pseudo process ids for span sessions, clear of real pids
SIZE_ATTR = re.compile(r"(?i)(count|size|length|rows|items|batch)")
COVER = 0.8  # a span explains a gap when it overlaps at least this share of it
DEPENDENCY_DIR = re.compile(r"/(node_modules|site-packages|dist-packages|vendor|\.cargo/registry|pkg/mod|\.m2|"
                            r"\.gradle|\.nuget|bower_components)/")


def _norm(q: str) -> str:
    q = re.sub(r"\(.*?\)", "", q or "")
    for a in ("::", "#", "/", "$", "\\"):
        q = q.replace(a, ".")
    return re.sub(r"\.+", ".", q).strip(".")


@dataclass
class FnRef:
    key: tuple[str, int]
    qualname: str
    file: str
    start: int
    end: int
    norm: str


class Mapper:
    """Span -> map function."""

    def __init__(self, map_data: dict, repo: Path):
        self.fns: list[FnRef] = []
        for f in map_data["functions"]:
            file, line, _ = f["id"].rsplit(":", 2)
            start, end = f["lines"]
            self.fns.append(FnRef((file, int(line)), f["qualname"], file, start, end, _norm(f["qualname"])))
        self.by_file: dict[str, list[FnRef]] = defaultdict(list)
        for fn in self.fns:
            self.by_file[fn.file].append(fn)
        self.repo = repo.resolve().as_posix().rstrip("/").lower() + "/"

    def rel(self, path: str) -> str | None:
        p = str(path).replace("\\", "/")
        inside = p.lower().startswith(self.repo)
        if inside:
            p = p[len(self.repo):]
        if p in self.by_file:
            return p
        if inside or DEPENDENCY_DIR.search("/" + p):
            return None  # an exact path that is not a mapped file: node_modules/x/index.js is not ./index.js
        best = None
        for f in self.by_file:  # longest suffix match: builds often run from another checkout
            if p.endswith("/" + f) and (best is None or len(f) > len(best)):
                best = f
        return best

    def by_location(self, path: str, line: int) -> FnRef | None:
        rel = self.rel(path)
        if rel is None:
            return None
        inside = [fn for fn in self.by_file[rel] if fn.start <= line <= fn.end]
        return min(inside, key=lambda fn: fn.end - fn.start) if inside else None

    def by_name(self, name: str) -> FnRef | None:
        n = _norm(name)
        if not n:
            return None
        exact = [fn for fn in self.fns if fn.norm == n]
        if len(exact) == 1:
            return exact[0]
        if "." in n:
            tail = [fn for fn in self.fns if fn.norm.endswith("." + n)]
            if len(tail) == 1:
                return tail[0]
        return None

    def match(self, s: otlp.Span) -> FnRef | None:
        a = s.attrs
        path = a.get("code.file.path") or a.get("code.filepath")
        line = a.get("code.line.number") or a.get("code.lineno")
        if path and line:
            try:
                hit = self.by_location(str(path), int(line))
            except (TypeError, ValueError):
                hit = None
            if hit:
                return hit
        ns, fn, fq = a.get("code.namespace"), a.get("code.function"), a.get("code.function.name")
        for cand in (fq, f"{ns}.{fn}" if ns and fn else None, fn):
            if cand and (hit := self.by_name(str(cand))):
                return hit
        if s.kind in (0, 1) and re.search(r"[.:/#]", s.name) and not re.match(r"^[A-Z]+ ", s.name):
            return self.by_name(s.name)
        return None


JVM_LANGS = {"java"}


def jvm_methods_include(map_data: dict, limit: int = 200) -> str | None:
    """OTEL_INSTRUMENTATION_METHODS_INCLUDE for the OpenTelemetry Java agent, derived from the map:
    every JVM method in a finding (its function and chain) plus entry points, as `pkg.Class[m1,m2];...`."""
    lang = {f["qualname"]: f["lang"] for f in map_data["functions"]}
    wanted = {f["qualname"] for f in map_data["functions"] if f.get("is_entry") and f["lang"] in JVM_LANGS}
    for f in map_data["findings"]:
        for q in [f["function"], *f.get("chain", [])]:
            if lang.get(q) in JVM_LANGS:
                wanted.add(q)
    by_class: dict[str, set[str]] = defaultdict(set)
    for q in sorted(wanted)[:limit]:
        cls, _, method = q.rpartition(".")
        if cls and cls.split(".")[-1][:1].isupper():  # skip top-level functions (no class to name)
            by_class[cls].add(method)
    return ";".join(f"{c}[{','.join(sorted(ms))}]" for c, ms in sorted(by_class.items())) or None


def io_info(s: otlp.Span) -> tuple[str, str] | None:
    """(system, target) for spans that are calls out of the program; None otherwise."""
    if s.kind in (2, 5):  # server / consumer: work coming in, not calls going out
        return None
    a = s.attrs
    if "url.full" in a or any(k.startswith("http.") for k in a):
        if s.kind not in (3, 4):
            return None
        method = a.get("http.request.method") or a.get("http.method") or ""
        url = a.get("url.full") or a.get("http.url") or (
            f"{a.get('server.address') or a.get('net.peer.name') or ''}{a.get('url.path') or ''}") or s.name
        return "http", f"{method} {url}".strip()
    if "db.system" in a or "db.system.name" in a:
        text = a.get("db.query.text") or a.get("db.statement") or a.get("db.operation.name") or s.name
        return "db", " ".join(str(text).split())[:160]
    if "rpc.system" in a:
        return "rpc", f"{a.get('rpc.service', '')}/{a.get('rpc.method', '')}".strip("/") or s.name
    if "messaging.system" in a:
        return "messaging", str(a.get("messaging.destination.name") or s.name)
    if s.kind in (3, 4):
        return "other", s.name
    return None


@dataclass
class _Rec:
    pid: int
    id: int
    parent: int
    start: int
    end: int
    depth: int
    fn: FnRef | None
    label: str
    is_io: bool


def _read_heartbeats(path: Path) -> tuple[dict, list[int]]:
    """Rows were filtered by the heartbeat regex when recorded; every row is one tick."""
    meta, times = {}, []
    for line in path.read_text(encoding="utf8").splitlines():
        if not line.strip():
            continue
        row = json.loads(line)
        if "meta" in row:
            meta = row["meta"]
        elif "t_ns" in row:
            times.append(int(row["t_ns"]))
    return meta, sorted(times)


def attach(trace: Trace, trace_dir: Path, map_data: dict, repo: Path) -> Trace:
    """Add span sessions (*.spans.jsonl) and heartbeat logs (*.heartbeat.jsonl) under trace_dir to `trace`."""
    span_files = sorted(trace_dir.glob("*.spans.jsonl"))
    hb_files = sorted(trace_dir.glob("*.heartbeat.jsonl"))
    if not span_files and not hb_files:
        return trace
    mapper = Mapper(map_data, repo)
    runs, calls, stalls, io = list(trace.runs), list(trace.calls), list(trace.stalls), list(trace.io)
    recs: list[_Rec] = []

    for idx, path in enumerate(span_files):
        spans = otlp.read_jsonl(path)
        pid = PID_BASE + idx
        ids = {s.span_id: i + 1 for i, s in enumerate(spans)}
        by_sid = {s.span_id: s for s in spans}
        child_time: dict[str, float] = defaultdict(float)
        for s in spans:
            if s.parent_id in ids:
                child_time[s.parent_id] += s.dur_s
        matched = {s.span_id: m for s in spans if (m := mapper.match(s))}

        def depth(s):
            d, cur, seen = 0, s, set()
            while cur.parent_id in by_sid and cur.parent_id not in seen:
                seen.add(cur.parent_id)
                cur = by_sid[cur.parent_id]
                d += 1
            return d

        def owner(s):  # the span itself or its nearest ancestor that is a repo function
            cur, seen = s, set()
            while cur is not None and cur.span_id not in seen:
                seen.add(cur.span_id)
                if cur.span_id in matched:
                    return matched[cur.span_id]
                cur = by_sid.get(cur.parent_id)
            return None

        for s in spans:
            sid, parent = ids[s.span_id], ids.get(s.parent_id, 0)
            info = io_info(s)
            if info and s.span_id not in matched:
                io.append(IoRec(pid, sid, parent, info[0], info[1], s.start_ns / 1e9, s.dur_s, s.status == 2))
                recs.append(_Rec(pid, sid, parent, s.start_ns, s.end_ns, depth(s), owner(s), f"{info[0]} {info[1]}", True))
                continue
            m = matched.get(s.span_id)
            self_s = max(0.0, s.dur_s - child_time.get(s.span_id, 0.0))
            shapes = {k: float(v) for k, v in s.attrs.items()
                      if isinstance(v, (int, float)) and not isinstance(v, bool) and SIZE_ATTR.search(k)}
            try:
                thread = int(s.attrs.get("thread.id") or 0)
            except (TypeError, ValueError):
                thread = 0
            calls.append(CallRec(pid, sid, parent, thread, m.key[0] if m else "", m.key[1] if m else 0,
                                 m.qualname if m else s.name, False, s.start_ns / 1e9, s.dur_s, self_s, 1, self_s, shapes))
            recs.append(_Rec(pid, sid, parent, s.start_ns, s.end_ns, depth(s), m or owner(s), s.name, False))
        res = spans[0].resource if spans else {}
        argv = otlp.read_meta(path).get("argv") or [path.name]
        runs.append({"pid": str(pid), "lang": str(res.get("telemetry.sdk.language", "otlp")), "source": "otlp",
                     "service": str(res.get("service.name", "")), "argv": json.dumps(argv),
                     "spans": str(len(spans)), "mapped": str(len(matched))})

    rec_by = {(r.pid, r.id): r for r in recs}

    def stack_of(r: _Rec) -> list[tuple[str, int, str]]:
        chain, cur, seen = [], r, set()
        while cur is not None and (cur.pid, cur.id) not in seen:
            seen.add((cur.pid, cur.id))
            if cur.fn is not None and (not chain or chain[-1][:2] != cur.fn.key):
                chain.append((cur.fn.key[0], cur.fn.key[1], cur.fn.qualname))
            cur = rec_by.get((cur.pid, cur.parent))
        return list(reversed(chain))

    for path in hb_files:
        meta, times = _read_heartbeats(path)
        threshold = float(meta.get("stall_ms", 100)) / 1000
        runs.append({"pid": "0", "lang": "any", "source": "heartbeat", "argv": json.dumps(meta.get("argv", [])),
                     "stall_threshold": str(threshold), "heartbeats": str(len(times))})
        if len(times) < 3:
            continue
        gaps = [b - a for a, b in zip(times, times[1:])]
        usual = median(gaps)
        for a, b in zip(times, times[1:]):
            excess = (b - a) - usual
            if excess < threshold * 1e9:
                continue
            w0, w1 = a + usual, b  # when the next tick was due, until it came
            covering = [r for r in recs if min(r.end, w1) - max(r.start, w0) >= COVER * (w1 - w0)]
            best = max(covering, key=lambda r: r.depth, default=None)
            if best is not None and best.fn is not None:
                stack = stack_of(best)
                if best.is_io:
                    stack.append(("", 0, best.label))
                stalls.append(StallRec(best.pid, best.id, best.fn.key[0], best.fn.key[1], best.fn.qualname,
                                       excess / 1e9, b / 1e9, stack))
            else:
                label = best.label if best is not None else "no span covered the gap"
                stalls.append(StallRec(0, 0, "", 0, f"(program-level: {label})", excess / 1e9, b / 1e9, []))

    return Trace(runs, calls, stalls, io, list(trace.counts))
