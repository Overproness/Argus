"""Python call tracer: records every activation of a repo function, in slices.

A function runs in slices: a sync function has one, a coroutine has one per
resume-to-await stretch. `self` time is slice time minus traced children. A
self slice longer than the stall threshold on a thread that is running an
asyncio loop is a stall: nothing else on that loop can make progress meanwhile.

Uses `sys.monitoring` (Python 3.12+): untraced code costs nothing after its
first event returns DISABLE.
"""
from __future__ import annotations

import atexit
import json
import linecache
import os
import re
import sys
import threading
import time
from pathlib import Path

from ..store import TraceWriter

CO_COROUTINE = 0x180  # CO_COROUTINE | CO_ASYNC_GENERATOR
NOISE_DIRS = ("/site-packages/", "/dist-packages/", "/.venv/", "/venv/", "/.audit/", "/node_modules/")
MAX_SHAPE_ARGS = 6


class _Act:
    """One activation of a repo function."""
    __slots__ = ("id", "parent", "code", "file", "line", "shapes", "start",
                 "self_dur", "slices", "max_self", "slice_start", "child_in_slice")

    def __init__(self, id, parent, code, file, line, shapes, now):
        self.id, self.parent, self.code, self.file, self.line, self.shapes = id, parent, code, file, line, shapes
        self.start = now
        self.self_dur = 0.0
        self.slices = 0
        self.max_self = 0.0
        self.slice_start = now
        self.child_in_slice = 0.0


def _shape(v):
    if isinstance(v, bool) or v is None:
        return None
    if isinstance(v, (int, float)):
        return abs(float(v))
    try:
        return float(len(v))
    except Exception:
        return None


def _def_line(path: str, first: int, name: str) -> int:
    """The `def` line of a function. `co_firstlineno` of a decorated function is its first decorator's line,
    while the static map (and every report) names functions by the `def` line."""
    rx = re.compile(rf"^\s*(async\s+)?def\s+{re.escape(name)}\b")
    for ln in range(first, first + 60):
        src = linecache.getline(path, ln)
        if not src:
            break
        if rx.match(src):
            return ln
    return first


class Tracer:
    def __init__(self, root: Path, writer: TraceWriter, stall_threshold: float, shapes: bool,
                 inject: dict[str, float] | None = None, inject_max: int = 25):
        self.root = str(root.resolve()).replace("\\", "/").rstrip("/") + "/"
        self.writer = writer
        self.threshold = stall_threshold
        self.want_shapes = shapes
        self._local = threading.local()
        self._info: dict = {}  # code -> (rel_file, line, is_coro) | None
        self._suspended: dict[int, _Act] = {}  # id(frame) of a paused coroutine/generator
        self._seq = 0
        self._lock = threading.Lock()
        # Latency injection: "file:def_line" -> seconds slept on entry to that function, for the first
        # `inject_max` activations. A sync function holding blocking I/O then stalls whatever thread runs it:
        # the asyncio loop thread (a stall on the exact stack: blocking-in-async proven) or a worker (no stall).
        self.inject = inject or {}
        self.inject_max = inject_max
        self._injected: dict[str, int] = {}

    # --- code filtering -----------------------------------------------------
    def info(self, code):
        try:
            return self._info[code]
        except KeyError:
            pass
        res = None
        # CO_OPTIMIZED (0x1) is set for functions only, not module or class bodies.
        if code.co_flags & 0x1 and not code.co_name.startswith("<") and not code.co_filename.startswith("<"):
            path = os.path.abspath(code.co_filename).replace("\\", "/")
            if path.startswith(self.root) and not any(d in path for d in NOISE_DIRS):
                res = (path[len(self.root):], _def_line(path, code.co_firstlineno, code.co_name),
                       bool(code.co_flags & CO_COROUTINE))
        self._info[code] = res
        return res

    def _stack(self) -> list:
        st = getattr(self._local, "stack", None)
        if st is None:
            st = self._local.stack = []
        return st

    # --- core ---------------------------------------------------------------
    def enter(self, frame, code, resumed: bool):
        inf = self.info(code)
        if inf is None:
            return False
        now = time.perf_counter()
        stack = self._stack()
        act = self._suspended.pop(id(frame), None) if resumed else None
        if act is None:
            with self._lock:
                self._seq += 1
                seq = self._seq
            shapes = self._shapes(frame, code) if self.want_shapes else None
            act = _Act(seq, stack[-1].id if stack else 0, code, inf[0], inf[1], shapes, now)
        act.slice_start = now
        act.child_in_slice = 0.0
        stack.append(act)
        if self.inject and not resumed:
            k = f"{inf[0]}:{inf[1]}"
            d = self.inject.get(k)
            if d and self._injected.get(k, 0) < self.inject_max:
                self._injected[k] = self._injected.get(k, 0) + 1
                time.sleep(d)  # counted in this activation's own slice, on the thread that called it
        return True

    def exit(self, frame, code, suspended: bool):
        if self.info(code) is None:
            return False
        now = time.perf_counter()
        stack = self._stack()
        if not stack or stack[-1].code is not code:
            return True  # activation started before tracing did
        act = stack.pop()
        slice_dur = now - act.slice_start
        self_slice = slice_dur - act.child_in_slice
        act.self_dur += self_slice
        act.slices += 1
        if self_slice > act.max_self:
            act.max_self = self_slice
        if stack:
            stack[-1].child_in_slice += slice_dur
        if self_slice >= self.threshold and self._loop_running():
            self.writer.add_stall((act.id, act.file, act.line, code.co_qualname, self_slice, now,
                                   json.dumps([(a.file, a.line, a.code.co_qualname) for a in (*stack, act)])))
        if suspended:
            self._suspended[id(frame)] = act
        else:
            self._finish(act, now)
        return True

    def _finish(self, act: _Act, now: float):
        self.writer.add_call((
            act.id, act.parent, threading.get_ident(), act.file, act.line, act.code.co_qualname,
            int(bool(act.code.co_flags & CO_COROUTINE)), act.start, now - act.start, act.self_dur,
            act.slices, act.max_self, json.dumps(act.shapes) if act.shapes else None,
        ))

    @staticmethod
    def _loop_running() -> bool:
        aio = sys.modules.get("asyncio")
        return aio is not None and aio._get_running_loop() is not None

    @staticmethod
    def _shapes(frame, code):
        n = code.co_argcount + code.co_kwonlyargcount
        if n == 0:
            return None
        loc = frame.f_locals
        out = {}
        for name in code.co_varnames[:min(n, MAX_SHAPE_ARGS)]:
            if name in ("self", "cls"):
                continue
            s = _shape(loc.get(name))
            if s is not None:
                out[name] = s
        return out or None

    def close(self):
        # Activations still open at exit (e.g. main) are recorded with what we know.
        now = time.perf_counter()
        for act in list(self._suspended.values()) + self._stack():
            self._finish(act, now)
        self.writer.close()


# --- backends ----------------------------------------------------------------

def _install_monitoring(tr: Tracer):
    m = sys.monitoring
    E = m.events
    tid = m.PROFILER_ID
    m.use_tool_id(tid, "argus")

    def on_start(code, off):
        if not tr.enter(sys._getframe(1), code, False):
            return m.DISABLE

    def on_resume(code, off):
        if not tr.enter(sys._getframe(1), code, True):
            return m.DISABLE

    def on_return(code, off, val):
        if not tr.exit(sys._getframe(1), code, False):
            return m.DISABLE

    def on_yield(code, off, val):
        if not tr.exit(sys._getframe(1), code, True):
            return m.DISABLE

    def on_unwind(code, off, exc):
        tr.exit(sys._getframe(1), code, False)

    def on_throw(code, off, exc):
        tr.enter(sys._getframe(1), code, True)

    for ev, cb in ((E.PY_START, on_start), (E.PY_RESUME, on_resume), (E.PY_RETURN, on_return),
                   (E.PY_YIELD, on_yield), (E.PY_UNWIND, on_unwind), (E.PY_THROW, on_throw)):
        m.register_callback(tid, ev, cb)
    m.set_events(tid, E.PY_START | E.PY_RESUME | E.PY_RETURN | E.PY_YIELD | E.PY_UNWIND | E.PY_THROW)
    return lambda: m.set_events(tid, 0)


def install(root: Path, out_dir: Path, stall_threshold: float = 0.1, shapes: bool = True,
            inject: dict[str, float] | None = None) -> Tracer:
    if not hasattr(sys, "monitoring"):
        raise RuntimeError("argus tracing needs Python 3.12+ (sys.monitoring)")
    out_dir.mkdir(parents=True, exist_ok=True)
    writer = TraceWriter(out_dir / f"{os.getpid()}-{int(time.time())}.db", {
        "lang": "python", "pid": str(os.getpid()), "root": str(root), "argv": json.dumps(sys.argv),
        "python": sys.version.split()[0], "started": str(time.time()), "stall_threshold": str(stall_threshold),
        "inject": json.dumps(inject or {}),
    })
    tr = Tracer(root, writer, stall_threshold, shapes, inject)
    stop = _install_monitoring(tr)

    def finish():
        stop()
        tr.close()
    atexit.register(finish)
    return tr


def install_from_env() -> Tracer | None:
    root, out = os.environ.get("AUDIT_TRACE_ROOT"), os.environ.get("AUDIT_TRACE_DIR")
    if not root or not out:
        return None
    inject = {k: float(v) / 1000 for k, v in json.loads(os.environ.get("AUDIT_TRACE_INJECT") or "{}").items()}
    return install(Path(root), Path(out), float(os.environ.get("AUDIT_TRACE_STALL_MS", "100")) / 1000,
                   os.environ.get("AUDIT_TRACE_SHAPES", "1") != "0", inject)
