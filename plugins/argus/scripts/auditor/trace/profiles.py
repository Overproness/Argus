"""Function-level runtime evidence from profilers and call-count coverage, for any language that has them.

Inputs (dropped into .audit/trace/ by `trace --node` or `trace-import --profile/--coverage`):
  V8 CPU profile (.cpuprofile)   Node (--cpu-prof), Deno, Chrome and Electron DevTools
  speedscope JSON                sampled or evented; exported by py-spy and speedscope itself
  V8 coverage (NODE_V8_COVERAGE) exact per-function call counts

A sampled profile is a timeline of stacks. From it:
  activations  a repo function's contiguous presence on the stack. Approximate: calls shorter than the
               sampling interval are missed and back-to-back calls merge, so runs are marked `sampled`
               and never used to count calls.
  stalls       stretches of consecutive busy samples on a loop thread longer than the threshold: nothing
               else ran on that thread (for Node: the event loop) meanwhile. Attributed to the deepest repo
               function present in at least 80% of the stretch, with its stack; the leaf (for example
               `spawnSync`) is appended so the blocking call is named.
Evented speedscope profiles record every open and close, so their activations are exact.
Coverage counts are exact and fill in `calls` where samples cannot.
"""
from __future__ import annotations

import json
import re
from collections import Counter, defaultdict
from dataclasses import dataclass
from pathlib import Path
from urllib.parse import unquote, urlparse

from .spans import FnRef, Mapper
from .store import CallRec, CountRec, StallRec, Trace

__all__ = ["attach", "coverage_counts", "from_cpuprofile", "from_speedscope", "FnRef"]

PID_BASE = 2_000_000_000
COVER = 0.8
IDLE = {"(idle)"}
# VM work with no JS stack (GC, internals) happens inside whatever JS was running: it continues that stretch.
CONTINUES = {"(garbage collector)", "(program)"}
# Optimized (inlined, OSR) code sometimes shows only the caller for a sample or two. A function's stretch
# survives absences this short; idle always ends it.
GAP_S = 0.005
LOOP_THREAD = re.compile(r"(?i)main|event|loop|ui\b|^thread 0$")
UNITS = {"seconds": 1.0, "milliseconds": 1e-3, "microseconds": 1e-6, "nanoseconds": 1e-9}


@dataclass(frozen=True)
class Frame:
    name: str
    file: str  # a path or file:// URL; "" when unknown
    line: int  # 1-based; 0 when unknown
    col: int = 0  # 1-based; 0 when unknown


@dataclass
class Timeline:
    """One thread's samples: (end time in s, duration in s, stack outer->inner as frame indexes)."""
    name: str
    frames: list[Frame]
    samples: list[tuple[float, float, tuple[int, ...]]]
    loop_thread: bool


# --- readers -----------------------------------------------------------------------------

def from_cpuprofile(doc: dict, name: str = "main", shift: float = 0.0) -> Timeline:
    """`shift` moves V8's monotonic timestamps onto another clock (see _clock_shift)."""
    nodes = {n["id"]: n for n in doc["nodes"]}
    parent = {c: n["id"] for n in doc["nodes"] for c in n.get("children", [])}
    frames: list[Frame] = []
    index: dict[tuple, int] = {}
    stacks: dict[int, tuple[int, ...]] = {}

    def frame_of(nid: int) -> int | None:
        cf = nodes[nid]["callFrame"]
        if cf.get("functionName") == "(root)":
            return None
        key = (cf.get("functionName") or "(anonymous)", cf.get("url", ""), int(cf.get("lineNumber", -1)) + 1,
               int(cf.get("columnNumber", -1)) + 1)
        if key not in index:
            index[key] = len(frames)
            frames.append(Frame(*key))
        return index[key]

    def stack(nid: int) -> tuple[int, ...]:
        if nid in stacks:
            return stacks[nid]
        chain, cur = [], nid
        while cur is not None:
            f = frame_of(cur)
            if f is not None:
                chain.append(f)
            cur = parent.get(cur)
        stacks[nid] = tuple(reversed(chain))
        return stacks[nid]

    t = doc["startTime"] / 1e6 + shift
    samples = []
    for sid, dt in zip(doc.get("samples", []), doc.get("timeDeltas", [])):
        dt_s = max(dt, 0) / 1e6
        t += dt_s
        samples.append((t, dt_s, stack(sid)))
    return Timeline(name, frames, samples, loop_thread=True)


def from_speedscope(doc: dict) -> tuple[list[Timeline], list[tuple[str, list[Frame], list[dict]]]]:
    """-> (sampled timelines, evented profiles as (name, frames, events)).

    Stalls need idle samples (empty stacks or "(idle)") to tell a blocked loop from ordinary work; a profile
    without them (py-spy without --idle, for example) gives activations only."""
    frames = [Frame(f.get("name", "?"), f.get("file", "") or "", int(f.get("line") or 0), int(f.get("col") or 0))
              for f in doc.get("shared", {}).get("frames", [])]
    sampled, evented = [], []
    profiles = doc.get("profiles", [])
    for p in profiles:
        scale = UNITS.get(p.get("unit", "none"))
        loop = len(profiles) == 1 or bool(LOOP_THREAD.search(p.get("name", "")))
        if p.get("type") == "sampled" and scale is not None:
            t = float(p.get("startValue", 0)) * scale
            out = []
            for stack, w in zip(p.get("samples", []), p.get("weights", [])):
                dt = float(w) * scale
                t += dt
                out.append((t, dt, tuple(stack)))
            has_idle = any(not st or frames[st[-1]].name in IDLE for _, _, st in out)
            sampled.append(Timeline(p.get("name", "thread"), frames, out, loop and has_idle))
        elif p.get("type") == "evented" and scale is not None:
            evs = [{"type": e["type"], "frame": e["frame"], "at": float(e["at"]) * scale} for e in p.get("events", [])]
            evented.append((p.get("name", "thread"), frames, evs))
    return sampled, evented


# --- mapping ---------------------------------------------------------------------------------

def _path(file: str) -> str:
    if file.startswith("file:"):
        p = unquote(urlparse(file).path)
        return p[1:] if re.match(r"^/[A-Za-z]:", p) else p
    return file


def _short(qualname: str) -> str:
    return qualname.rsplit(".", 1)[-1].rsplit("::", 1)[-1]


class FrameMapper:
    def __init__(self, mapper: Mapper):
        self.m = mapper
        self.cache: dict[Frame, FnRef | None] = {}

    def __call__(self, f: Frame) -> FnRef | None:
        if f in self.cache:
            return self.cache[f]
        hit = None
        path = _path(f.file)
        if f.name == "(anonymous)" and (f.line, f.col) == (1, 1):
            pass  # a module's top-level code (V8 wraps it in a function at 1:1), not a function declared there
        elif path.startswith(("node:", "internal/")):
            pass  # the runtime's own code
        elif path:
            hit = self.m.by_location(path, f.line) if f.line else None
            if hit is None or (f.name and _short(hit.qualname) != f.name.split(".")[-1]
                               and not f.name.startswith("(")):
                rel = self.m.rel(path)  # names survive transpilers even when line numbers do not
                named = [fn for fn in self.m.by_file.get(rel, [])
                         if fn.norm.rsplit(".", 1)[-1] == f.name.split(".")[-1]] if rel else []
                hit = named[0] if len(named) == 1 else hit
        elif f.name and not f.name.startswith("("):
            hit = self.m.by_name(f.name)
        self.cache[f] = hit
        return hit


# --- samples -> activations and stalls ------------------------------------------------------------

def _from_samples(tl: Timeline, fmap: FrameMapper, pid: int, threshold: float, calls: list, stalls: list):
    open_runs: dict[tuple[str, int], list] = {}  # key -> [id, parent, start, dur, self, fn]
    seq = 0
    done = []

    def close(key):
        r = open_runs.pop(key)
        done.append(r)

    busy: list[tuple[float, float, tuple[int, ...]]] = []

    def repo_chain(st) -> list[FnRef]:
        out: list[FnRef] = []
        for fi in st:
            fn = fmap(tl.frames[fi])
            if fn is not None and fn not in out:
                out.append(fn)
        return out

    def segment_stalls() -> bool:
        """Within one busy stretch, each function continuously on the stack for longer than the threshold is a
        stall of its own, unless deeper such functions account for most of it (then those are the culprits)."""
        chains: list[list[FnRef]] = []
        for _, _, st in busy:
            ch = repo_chain(st)
            if not ch and st and tl.frames[st[-1]].name in CONTINUES and chains:
                ch = chains[-1]
            chains.append(ch)
        segs = []  # (key, fn, first, last, dur)
        open_at: dict = {}  # key -> [first, last_present, fn, absent_s]
        end = len(chains)
        for i, ch in enumerate(chains + [[]]):
            keys = {fn.key: fn for fn in ch}
            dt = busy[i][1] if i < end else 0.0
            for k in list(open_at):
                if k in keys:
                    continue
                open_at[k][3] += dt
                if i == end or open_at[k][3] > GAP_S:
                    first, last, fn, _ = open_at.pop(k)
                    dur = sum(busy[j][1] for j in range(first, last + 1))
                    if dur >= threshold:
                        segs.append((k, fn, first, last, dur))
            for k, fn in keys.items():
                if k in open_at:
                    open_at[k][1], open_at[k][3] = i, 0.0
                else:
                    open_at[k] = [i, i, fn, 0.0]
        def below(t, s) -> bool:
            """t's function runs inside s's call: deeper on the stack wherever both are present."""
            for j in range(t[2], t[3] + 1):
                ks = [fn.key for fn in chains[j]]
                if t[0] in ks and s[0] in ks:
                    return ks.index(t[0]) > ks.index(s[0])
            return False

        kept = []
        for s in segs:
            inner = set()
            for t in segs:  # a caller's segment can span exactly its callee's: nesting in time is not enough
                if t is not s and t[0] != s[0] and s[2] <= t[2] and t[3] <= s[3] and below(t, s):
                    inner.update(range(t[2], t[3] + 1))
            inner_dur = sum(busy[j][1] for j in inner)
            if inner_dur < COVER * s[4]:
                kept.append(s)
        for key, fn, first, last, dur in kept:
            chain, rep = [], chains[first]
            for f in rep:
                chain.append((f.key[0], f.key[1], f.qualname))
                if f.key == key:
                    break
            leaf = Counter(tl.frames[busy[j][2][-1]].name for j in range(first, last + 1) if busy[j][2]).most_common(1)
            if leaf and leaf[0][0] != fn.qualname.rsplit(".", 1)[-1]:
                chain.append(("", 0, f"{leaf[0][0]} (sampled leaf)"))
            stalls.append(StallRec(pid, 0, key[0], key[1], fn.qualname, dur, busy[last][0], chain))
        return bool(kept)

    def flush_busy():
        dur = sum(dt for _, dt, _ in busy)
        if dur >= threshold and tl.loop_thread and not segment_stalls():
            per_key: Counter = Counter()
            depth: dict = defaultdict(int)
            for _, _, st in busy:
                seen = set()
                for pos, fi in enumerate(st):
                    fn = fmap(tl.frames[fi])
                    if fn is not None and fn.key not in seen:
                        seen.add(fn.key)
                        per_key[fn.key] += 1
                        depth[fn.key] = max(depth[fn.key], pos)
            cands = [k for k, n in per_key.items() if n >= COVER * len(busy)]
            leaf = Counter(tl.frames[st[-1]].name for _, _, st in busy if st).most_common(1)
            leaf_name = leaf[0][0] if leaf else "?"
            if cands:
                best = max(cands, key=lambda k: depth[k])

                def has(st):
                    return any((fn := fmap(tl.frames[fi])) is not None and fn.key == best for fi in st)

                rep = next(st for _, _, st in busy if has(st))
                chain, seen = [], set()
                for fi in rep:
                    fn = fmap(tl.frames[fi])
                    if fn is not None and fn.key not in seen:
                        seen.add(fn.key)
                        chain.append((fn.key[0], fn.key[1], fn.qualname))
                    if fn is not None and fn.key == best:
                        break
                fn = next(f for f in (fmap(tl.frames[fi]) for fi in rep) if f is not None and f.key == best)
                if leaf_name != fn.qualname.rsplit(".", 1)[-1]:
                    chain.append(("", 0, f"{leaf_name} (sampled leaf)"))
                stalls.append(StallRec(pid, 0, best[0], best[1], fn.qualname, dur, busy[-1][0], chain))
            elif seen_idle:  # before the loop first idles, program-level work is startup (loading modules)
                stalls.append(StallRec(pid, 0, "", 0, f"(program-level: {leaf_name})", dur, busy[-1][0], []))
        busy.clear()

    prev_chain: list[FnRef] = []
    absent: dict = {}
    seen_idle = False
    for t, dt, st in tl.samples:
        idle = not st or tl.frames[st[-1]].name in IDLE
        if idle:
            flush_busy()
            seen_idle = True
        else:
            busy.append((t, dt, st))
        chain = repo_chain(st)
        if not chain and not idle and st and tl.frames[st[-1]].name in CONTINUES:
            chain = prev_chain
        prev_chain = chain
        present = {fn.key for fn in chain}
        for key in [k for k in open_runs if k not in present]:
            absent[key] = absent.get(key, 0.0) + dt
            if idle or absent[key] > GAP_S:
                absent.pop(key, None)
                close(key)
            else:
                open_runs[key][3] += dt  # a short absence inside the stretch still counts toward it
        for i, fn in enumerate(chain):
            absent.pop(fn.key, None)
            run = open_runs.get(fn.key)
            if run is None:
                seq += 1
                parent = open_runs[chain[i - 1].key][0] if i and chain[i - 1].key in open_runs else 0
                run = open_runs[fn.key] = [seq, parent, t - dt, 0.0, 0.0, fn]
            run[3] += dt
        if chain:
            open_runs[chain[-1].key][4] += dt
    flush_busy()
    for key in list(open_runs):
        close(key)
    for rid, parent, start, dur, self_s, fn in done:
        calls.append(CallRec(pid, rid, parent, 0, fn.key[0], fn.key[1], fn.qualname, False, start, dur, self_s, 1,
                             self_s, {}))


def _from_events(name: str, frames: list[Frame], events: list[dict], fmap: FrameMapper, pid: int, calls: list):
    """Exact activations from evented profiles (open/close pairs)."""
    stack: list[list] = []  # [id, parent, start, child_time, fn or None]
    seq = 0
    for e in sorted(events, key=lambda e: e["at"]):
        if e["type"] == "O":
            seq += 1
            fn = fmap(frames[e["frame"]])
            parent = next((s[0] for s in reversed(stack) if s[4] is not None), 0)
            stack.append([seq, parent, e["at"], 0.0, fn])
        elif e["type"] == "C" and stack:
            rid, parent, start, child, fn = stack.pop()
            dur = e["at"] - start
            if stack:
                stack[-1][3] += dur
            if fn is not None:
                self_s = max(0.0, dur - child)
                calls.append(CallRec(pid, rid, parent, 0, fn.key[0], fn.key[1], fn.qualname, False, start, dur,
                                     self_s, 1, self_s, {}))


# --- coverage -> exact counts -----------------------------------------------------------------

def _line_of(src: str, offset: int, cache: dict) -> int:
    starts = cache.get(id(src))
    if starts is None:
        starts = [0] + [i + 1 for i, ch in enumerate(src) if ch == "\n"]
        cache[id(src)] = starts
    lo, hi = 0, len(starts)
    while lo + 1 < hi:
        mid = (lo + hi) // 2
        if starts[mid] <= offset:
            lo = mid
        else:
            hi = mid
    return lo + 1


def coverage_counts(cov_files: list[Path], mapper: Mapper) -> list[CountRec]:
    """Exact call counts. Within one script, each map function takes the one V8 function that is clearly it (the
    same name, else the same line extent); inner closures and the module wrapper share its lines but are not it.
    Counts from different processes add up."""
    counts: Counter = Counter()
    names: dict = {}
    sources: dict[str, str] = {}
    line_cache: dict = {}
    for f in cov_files:
        try:
            doc = json.loads(f.read_text(encoding="utf8"))
        except (json.JSONDecodeError, OSError):
            continue
        for script in doc.get("result", []):
            url = script.get("url", "")
            if not url.startswith("file:"):
                continue
            path = _path(url)
            if mapper.rel(path) is None:
                continue
            if path not in sources:
                try:
                    sources[path] = Path(path).read_text(encoding="utf8", errors="replace")
                except OSError:
                    continue
            src = sources[path]
            best: dict = {}  # map key -> ((same name, same extent), count)
            for fn in script.get("functions", []):
                r0 = fn["ranges"][0]
                start = _line_of(src, r0["startOffset"], line_cache)
                end = _line_of(src, max(r0["startOffset"], r0["endOffset"] - 1), line_cache)
                hit = mapper.by_location(path, start)
                if hit is None:
                    continue
                name = fn.get("functionName") or ""
                score = (bool(name) and _short(hit.qualname) == name.split(".")[-1],
                         (hit.start, hit.end) == (start, end))
                if any(score) and (hit.key not in best or score > best[hit.key][0]):
                    best[hit.key] = (score, int(r0.get("count", 0)))
                    names[hit.key] = hit.qualname
            for k, (_, n) in best.items():
                counts[k] += n
    return [CountRec(k[0], k[1], names[k], n, "v8-coverage") for k, n in counts.items()]


# --- entry point ------------------------------------------------------------------------------------

def _clock_shift(meta: dict, start_s: float) -> float | None:
    """Seconds to add to a V8 profile's times to put them on the epoch clock that spans and heartbeats use.
    V8 stamps profiles with the monotonic clock Python's perf_counter reads (QPC, CLOCK_MONOTONIC, mach time),
    so the (epoch, perf_counter) pair `trace` records at launch ties the two together."""
    if "epoch_ns" not in meta or "mono_ns" not in meta:
        return None
    mono0 = meta["mono_ns"] / 1e9
    if not mono0 - 1.0 <= start_s <= mono0 + 7 * 86400:
        return None  # not the same clock after all, or a profile from another run
    return meta["epoch_ns"] / 1e9 - mono0


def _overlap(a: StallRec, b: StallRec) -> float:
    return min(a.at, b.at) - max(a.at - a.dur, b.at - b.dur)


def attach(trace: Trace, trace_dir: Path, map_data: dict, repo: Path, stall_s: float = 0.1) -> Trace:
    prof_files = sorted([*trace_dir.glob("node-prof/**/*.cpuprofile"), *trace_dir.glob("profiles/*.cpuprofile"),
                         *trace_dir.glob("profiles/*.speedscope.json")])
    cov_files = sorted([*trace_dir.glob("node-cov/**/*.json"), *trace_dir.glob("coverage/*.json")])
    if not prof_files and not cov_files:
        return trace
    mapper = Mapper(map_data, repo)
    fmap = FrameMapper(mapper)
    runs, calls = list(trace.runs), list(trace.calls)
    new_stalls, on_epoch = [], []
    pid = PID_BASE
    metas: dict[Path, dict] = {}
    for f in prof_files:
        try:
            doc = json.loads(f.read_text(encoding="utf8"))
        except (json.JSONDecodeError, OSError):
            continue
        if f.parent not in metas:  # written by `trace` for each Node run: its command and a clock pair
            mf = f.parent / "_argus_meta.json"
            metas[f.parent] = json.loads(mf.read_text(encoding="utf8")) if mf.exists() else {}
        meta = metas[f.parent]
        traced_cmd = meta.get("argv", [])
        shift = None
        if f.name.endswith(".cpuprofile"):
            shift = _clock_shift(meta, doc.get("startTime", 0) / 1e6)
            timelines, evented = [from_cpuprofile(doc, shift=shift or 0.0)], []
            source, lang = "v8-cpuprofile", "javascript"
        else:
            timelines, evented = from_speedscope(doc)
            source, lang = "speedscope", "any"
        m = re.match(r"CPU\.\d+\.\d+\.(\d+)\.", f.name)  # Node names profiles CPU.<date>.<time>.<pid>.<tid>.<n>
        label = (traced_cmd + [f"(process {m.group(1)})"]) if traced_cmd and m else [f.name]
        for tl in timelines:
            pid += 1
            got_calls, got_stalls = [], []
            _from_samples(tl, fmap, pid, stall_s, got_calls, got_stalls)
            if not got_calls:
                continue  # never ran repo code (npm itself, a helper process): its busy time is not the repo's
            calls += got_calls
            new_stalls += got_stalls
            if shift is not None:
                on_epoch += got_stalls
            runs.append({"pid": str(pid), "lang": lang, "source": source, "sampled": "1",
                         "argv": json.dumps(label if f.name.endswith(".cpuprofile") else [f.name, tl.name]),
                         "stall_threshold": str(stall_s), "samples": str(len(tl.samples)),
                         "activations": str(len(got_calls)), "stalls": "1" if tl.loop_thread else "0",
                         "clock": "epoch" if shift is not None else "profile"})
        for name, frames, events in evented:
            pid += 1
            _from_events(name, frames, events, fmap, pid, calls)
            runs.append({"pid": str(pid), "lang": lang, "source": source + " (evented)", "stalls": "0",
                         "argv": json.dumps([f.name, name]), "stall_threshold": str(stall_s)})
    # A heartbeat gap nothing could attribute is the same event as a profile stall at the same time: keep the
    # profile's version, which names the function.
    stalls = [s for s in trace.stalls
              if s.file or not any(_overlap(s, p) >= 0.5 * min(s.dur, p.dur) for p in on_epoch)] + new_stalls
    counts = list(trace.counts) + coverage_counts(cov_files, mapper)
    return Trace(runs, calls, stalls, list(trace.io), counts)
