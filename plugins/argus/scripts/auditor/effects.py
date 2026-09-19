"""Effects that cross function boundaries: waits, deadlines, retries, crashes.

Latency and failures travel from callees to callers; deadlines and retries
change them on the way. All numbers are static upper bounds:

  wait(site)   how long one call site can hold its caller: a boundary's timeout
               (explicit, client-level or the library default), a sleep's
               duration, or unbounded. An internal call inherits its callee's
               worst wait, capped by a deadline the caller wraps around it,
               unless the callee blocks the thread, in which case the deadline
               cannot fire.
  attempts     retry multipliers: retry loops around the site and retry
               decorators on the function.

Findings produced here:
  deadline-cannot-preempt   a deadline wraps code that blocks the thread/loop, so it never fires on time
  timeout-budget-exceeded   the inner call can wait longer than the caller's deadline
  hang-reaches-entry        an entry point can wait forever on an unbounded call
  retry-without-backoff     retries with no delay between attempts
  unbounded-retry           retries that never give up
  retry-amplification       nested retries multiply the attempts per request
  panic-on-io-error         a network/DB error crashes the task (unwrap/expect, try!)
  ignored-io-error          the error of a network/DB call is discarded (Go `_`)
"""
from __future__ import annotations

import re
import sys
from collections import defaultdict

from .langs.base import DB, NET, SLEEP, WAIT
from .model import Boundary, Edge, Finding, Function, LoopInfo
from .timeouts import fmt, parse_default, sleep_duration

INF = float("inf")
BOUNDED = 0.0  # bounded by a timeout whose value we could not read
IO = (NET, DB)
# (pattern, where to look, rule, message). "tail" is the code applied to the call's result.
CRASH = {
    "rust": (re.compile(r"\.(unwrap|expect)\("), "tail", "panic-on-io-error",
             "If this {kind} call fails, `unwrap`/`expect` panics. In a tokio task the task dies, silently "
             "unless its JoinHandle is checked; on the main path the process exits. Timeouts, resets and 5xx "
             "responses are routine for remote calls, so handle the error or propagate it with `?`."),
    "swift": (re.compile(r"\btry!"), "snippet", "panic-on-io-error",
              "If this {kind} call fails, `try!` crashes the process. Handle the error with `try`/`catch`."),
    "go": (re.compile(r",\s*_\s*:?=|^\s*_\s*:?="), "snippet", "ignored-io-error",
           "The error of this {kind} call is discarded (`_`). On failure the result is nil or zero and the "
           "next use misbehaves or panics far from the cause."),
}


class Effects:
    def __init__(self, m):
        self.m = m
        self.fns: dict[str, Function] = m.functions
        self.b_at: dict[tuple[str, int], Boundary] = {}
        for b in m.boundaries:
            self.b_at.setdefault((b.function, b.call.line), b)
        self.e_at: dict[tuple[str, int], list[Edge]] = defaultdict(list)
        for e in m.edges:
            self.e_at[(e.caller, e.line)].append(e)
        self._wait: dict[str, tuple[float, list[str], str]] = {}
        self._amp: dict[str, tuple[float, list[str], str | None]] = {}
        self._ablock: dict[str, list[str] | None] = {}
        self._retry_loops: dict[str, list[tuple[LoopInfo, int | None]]] = {}
        self.found: list[Finding] = []
        self.summary: dict = {"entries": [], "deadlines": [], "retries": []}
        self.configured = {m.family_of(f.lang) for f in m.files.values() if f.client_timeout}

    # --- boundaries -------------------------------------------------------------
    def boundary_wait(self, b: Boundary) -> tuple[float, str]:
        fn = self.fns[b.function]
        c = b.call
        if b.category == SLEEP:
            d = sleep_duration(c.snippet, fn.lang)
            return (d or 0.0), f"sleep {fmt(d)}"
        if b.category not in (NET, DB, WAIT):
            return 0.0, b.kind
        if c.has_timeout:
            return (c.timeout_s if c.timeout_s is not None else BOUNDED), f"timeout {fmt(c.timeout_s)}"
        fctx = self.m.files[fn.file]
        if fctx.client_timeout and not b.default_client:
            return (fctx.client_timeout_s or BOUNDED), f"client timeout {fmt(fctx.client_timeout_s)}"
        d = parse_default(b.default_timeout)
        if d is not None:
            return d, f"library default {fmt(d)}"
        if (b.client_level and b.confidence != "exact" and not b.default_client and not fctx.untimed_client
                and self.m.family_of(fn.lang) in self.configured):
            # Same rule as io-without-timeout: the client may carry a timeout configured elsewhere.
            return BOUNDED, "client timeout possibly configured elsewhere"
        return INF, "no timeout"

    def _io_in(self, fn: Function, lo: int, hi: int) -> bool:
        for c in fn.calls:
            if not lo <= c.line <= hi:
                continue
            b = self.b_at.get((fn.id, c.line))
            if b is not None and b.category in IO:
                return True
            if any(e.callee in self.m.io_reach for e in self.e_at.get((fn.id, c.line), [])):
                return True
        return False

    def retry_loops(self, fn: Function) -> list[tuple[LoopInfo, int | None]]:
        """Loops that retry I/O: (loop, attempts); attempts None = unbounded, 0 = finite but unknown."""
        if fn.id in self._retry_loops:
            return self._retry_loops[fn.id]
        out = []
        for lp in fn.loops:
            if lp.kind == "collection" or not lp.handles_errors:
                continue
            if not (lp.retryish or (lp.exits and (lp.sleep_s is not None or lp.kind == "counted"))):
                continue
            if not self._io_in(fn, lp.line, lp.end_line):
                continue
            if lp.kind == "forever" or (lp.kind == "conditional" and lp.bound is None):
                attempts = 0 if lp.policy_bound else None  # a counter or retry policy bounds it
            else:
                attempts = lp.bound or 0
            if attempts == 1:
                continue  # one attempt is not a retry
            out.append((lp, attempts))
        self._retry_loops[fn.id] = out
        return out

    def _site_mult(self, fn: Function, line: int) -> tuple[float, list[tuple[str, str]], float]:
        """Retry multiplier around one call site, its layers as (description, factor), and added backoff."""
        mult, layers, delay = 1.0, [], 0.0
        for lp, att in self.retry_loops(fn):
            if lp.line <= line <= lp.end_line:
                n = INF if att is None else (att or 1)
                mult *= n
                factor = "∞" if att is None else (str(att) if att else "?")
                layers.append((f"{fn.qualname} retry loop ×{factor} ({fn.file}:{lp.line})", factor))
                if lp.sleep_s and n != INF:
                    delay += (n - 1) * lp.sleep_s
        return mult, layers, delay

    # --- blocking under a deadline -------------------------------------------------
    def async_blocks(self, fid: str) -> list[str] | None:
        """For an async function: the path by which it blocks its thread/loop, or None."""
        if fid in self._ablock:
            return self._ablock[fid]
        self._ablock[fid] = None  # cycle guard
        fn = self.fns[fid]
        res = None
        if fn.is_async:
            for c in fn.calls:
                b = self.b_at.get((fid, c.line))
                if b is not None and b.blocking and c.context == "async":
                    res = [fn.qualname, f"{b.kind} @ {fn.file}:{c.line}"]
                    break
                for e in self.e_at.get((fid, c.line), []):
                    callee = self.fns[e.callee]
                    if e.context != "async":
                        continue
                    if not callee.is_async and e.callee in self.m.blocks:
                        res = [fn.qualname, *self.m._block_path(e.callee)[0]]
                    elif callee.is_async and (sub := self.async_blocks(e.callee)):
                        res = [fn.qualname, *sub]
                    if res:
                        break
                if res:
                    break
        self._ablock[fid] = res
        return res

    def cannot_preempt(self, e: Edge) -> list[str] | None:
        if e.context == "offloaded":
            return None
        callee = self.fns[e.callee]
        if not callee.is_async and e.callee in self.m.blocks:
            return self.m._block_path(e.callee)[0]
        if callee.is_async:
            return self.async_blocks(e.callee)
        return None

    # --- waits ------------------------------------------------------------------------
    def wait(self, fid: str) -> tuple[float, list[str], str]:
        """Worst time one activation can wait: (seconds, path, formula)."""
        if fid in self._wait:
            return self._wait[fid]
        self._wait[fid] = (0.0, [], "")  # cycle guard: recursion adds no new wait
        fn = self.fns[fid]
        best: tuple[float, list[str], str] = (0.0, [], "")
        for c in fn.calls:
            cands: list[tuple[float, list[str], str]] = []
            b = self.b_at.get((fid, c.line))
            if b is not None:
                w, why = self.boundary_wait(b)
                cands.append((w, [f"{b.kind} @ {fn.file}:{c.line} ({why})"], why))
            for e in self.e_at.get((fid, c.line), []):
                w, path, formula = self.wait(e.callee)
                path = [self.fns[e.callee].qualname, *path]
                if e.timeout_s is not None and not self.cannot_preempt(e) and w > e.timeout_s:
                    w, formula = e.timeout_s, f"capped by {fmt(e.timeout_s)} deadline"
                    path = path + [f"(capped by the {fmt(e.timeout_s)} deadline at {fn.file}:{c.line})"]
                cands.append((w, path, formula))
            if not cands:
                continue
            w, path, formula = max(cands, key=lambda x: x[0])
            if w <= 0:
                continue
            mult, layers, delay = self._site_mult(fn, c.line)
            if mult != 1:
                total = w * mult + delay
                formula = f"{'∞' if mult == INF else int(mult)} attempts × {fmt(w)}" + (
                    f" + {fmt(delay)} backoff" if delay else "")
                path = [d for d, _ in layers] + path
            else:
                total = w
            if total > best[0]:
                best = (total, path, formula)
        if fn.retry and best[0] > 0:
            att = fn.retry[0]
            n = INF if att is None else att
            best = (best[0] * n, best[1], f"{'∞' if att is None else att} decorator attempts × ({best[2]})")
        self._wait[fid] = best
        return best

    # --- retry amplification --------------------------------------------------------------
    def amp(self, fid: str) -> tuple[float, list[tuple[str, str]], str | None]:
        """Most attempts against one I/O boundary from one activation: (count, layers, leaf)."""
        if fid in self._amp:
            return self._amp[fid]
        self._amp[fid] = (0.0, [], None)
        fn = self.fns[fid]
        best: tuple[float, list[tuple[str, str]], str | None] = (0.0, [], None)
        for c in fn.calls:
            cands = []
            b = self.b_at.get((fid, c.line))
            if b is not None and b.category in IO:
                cands.append((1.0, [], f"{b.kind} @ {fn.file}:{c.line}"))
            for e in self.e_at.get((fid, c.line), []):
                if e.callee in self.m.io_reach:
                    a, ls, leaf = self.amp(e.callee)
                    if a > 0:
                        cands.append((a, ls, leaf))
            if not cands:
                continue
            mult, layers, _ = self._site_mult(fn, c.line)
            for a, ls, leaf in cands:
                if mult * a > best[0]:
                    best = (mult * a, layers + ls, leaf)
        if fn.retry and best[0] > 0:
            att = fn.retry[0]
            factor = "∞" if att is None else str(att)
            best = (best[0] * (INF if att is None else att),
                    [(f"{fn.qualname} @retry ×{factor} ({fn.file}:{fn.line})", factor)] + best[1], best[2])
        self._amp[fid] = best
        return best

    # --- findings ---------------------------------------------------------------------------
    def run(self) -> "Effects":
        limit = sys.getrecursionlimit()
        sys.setrecursionlimit(max(limit, 20000))
        try:
            self._deadlines()
            self._retries()
            self._entries()
            self._crashes()
        finally:
            sys.setrecursionlimit(limit)
        return self

    def _add(self, rule, sev, conf, fn: Function, line, msg, chain=None, reached=True):
        self.found.append(self.m._finding(rule, sev, conf, fn, line, msg, chain=chain,
                                          reached_from=self.m._roots(fn.id) if reached else None))

    def _deadlines(self):
        for e in self.m.edges:
            if e.timeout_s is None:
                continue
            caller, callee = self.fns[e.caller], self.fns[e.callee]
            D = e.timeout_s
            row = {"caller": caller.qualname, "callee": callee.qualname, "location": f"{caller.file}:{e.line}",
                   "deadline_s": D, "inner_wait_s": None, "inner_wait": "blocks the thread", "status": "ok"}
            blocked = self.cannot_preempt(e)
            if blocked:
                row["status"] = "cannot-preempt"
                self._add("deadline-cannot-preempt", "high", e.confidence, caller, e.line,
                          f"The {fmt(D)} deadline around `{callee.qualname}` cannot fire while it blocks the "
                          "thread it runs on, so a stalled call holds the caller for its full duration. "
                          "Offload the blocking part (spawn_blocking / to_thread) or make it async.",
                          chain=[caller.qualname, *blocked])
            else:
                w, path, formula = self.wait(e.callee)
                row["inner_wait_s"] = None if w in (INF, BOUNDED) else round(w, 3)
                row["inner_wait"] = ("unbounded" if w == INF else "bounded, value unknown" if w == BOUNDED
                                     else f"{fmt(w)}" + (f" ({formula})" if formula else ""))
                offloaded = e.context == "offloaded"
                if w == INF and offloaded:
                    row["status"] = "abandons-thread"
                    self._add("timeout-budget-exceeded", "medium", e.confidence, caller, e.line,
                              f"The {fmt(D)} deadline gives up on `{callee.qualname}`, but the offloaded thread "
                              f"keeps waiting with no bound ({formula or 'no timeout'}). Threads cannot be "
                              "cancelled, so every timeout leaks one blocked worker.",
                              chain=[caller.qualname, *path])
                elif D < w < INF:
                    row["status"] = "exceeded"
                    note = (" The offloaded thread keeps running after the deadline fires."
                            if offloaded else " The inner request keeps its connection until its own timeout.")
                    self._add("timeout-budget-exceeded", "medium", e.confidence, caller, e.line,
                              f"`{callee.qualname}` can take up to {fmt(w)} ({formula}) but the caller's "
                              f"deadline is {fmt(D)}: the caller gives up first and the work is wasted.{note} "
                              "Make the inner timeouts and retries fit inside the outer deadline.",
                              chain=[caller.qualname, *path])
            self.summary["deadlines"].append(row)

    def _retries(self):
        best_by_leaf: dict[str, tuple[float, Finding]] = {}
        for fn in self.fns.values():
            layers_here = []
            for lp, att in self.retry_loops(fn):
                layers_here.append(("loop", lp.line, att, "none" if lp.sleep_s is None else
                                    ("exponential" if lp.exponential else "fixed")))
            if fn.retry and fn.lang in ("python", "java", "kotlin", "scala", "ruby"):
                if self._io_in(fn, fn.line, fn.end_line):
                    layers_here.append(("decorator", fn.line, fn.retry[0], fn.retry[1]))
            for kind, line, att, backoff in layers_here:
                n = "forever" if att is None else (f"{att}×" if att else "a fixed number of times")
                self.summary["retries"].append({
                    "function": fn.qualname, "location": f"{fn.file}:{line}", "kind": kind,
                    "attempts": att, "backoff": backoff})
                if att is None:
                    self._add("unbounded-retry", "high" if backoff == "none" else "medium", "heuristic", fn, line,
                              f"Retries I/O {n}" + (" with no delay between attempts" if backoff == "none" else "")
                              + ". During an outage this never gives up: it hammers the dependency (rate "
                              "limits, bans) and holds the caller indefinitely. Cap the attempts.")
                elif backoff == "none":
                    self._add("retry-without-backoff", "medium", "heuristic", fn, line,
                              f"Retries I/O {n} with no delay between attempts. Failures from an overloaded or "
                              "rate-limited dependency come back immediately and get retried immediately. Add "
                              "exponential backoff with jitter.")
            if not layers_here:
                continue
            a, layers, leaf = self.amp(fn.id)
            if a != INF and a >= 9 and len(layers) >= 2 and leaf:
                f = self.m._finding(
                    "retry-amplification", "high" if a >= 27 else "medium", "heuristic", fn, fn.line,
                    f"One call to `{fn.qualname}` can make up to {int(a)} attempts against {leaf} "
                    f"({' × '.join(factor for _, factor in layers)} across {len(layers)} retry layers). "
                    "During an outage every layer retries the layer below it. Retry in one layer only and "
                    "make the others fail fast.",
                    chain=[d for d, _ in layers] + [leaf], reached_from=self.m._roots(fn.id))
                prev = best_by_leaf.get(leaf)
                if prev is None or a > prev[0]:
                    best_by_leaf[leaf] = (a, f)
        self.found += [f for _, f in best_by_leaf.values()]

    def _entries(self):
        entries = [f for f in self.fns.values() if f.is_entry]
        for f in self.fns.values():  # service loops: async roots running forever over I/O
            if (not f.is_entry and f.is_async and not self.m.in_edges[f.id]
                    and any(lp.kind == "forever" for lp in f.loops) and f.id in self.m.io_reach):
                entries.append(f)
        for fn in entries:
            w, path, formula = self.wait(fn.id)
            a, _, _ = self.amp(fn.id)
            self.summary["entries"].append({
                "function": fn.qualname, "location": f"{fn.file}:{fn.line}",
                "max_wait": fmt(w) if w else "none", "max_wait_s": None if w == INF else round(w, 3),
                "path": path, "formula": formula,
                "max_attempts_per_request": None if a == INF else int(a)})
            if w == INF:
                self._add("hang-reaches-entry", "medium", "heuristic", fn, fn.line,
                          f"`{fn.qualname}` can wait forever: {' → '.join([fn.qualname, *path])}. No timeout or "
                          "deadline bounds this path, so one unresponsive peer freezes this entry point.",
                          chain=[fn.qualname, *path], reached=False)

    def _crashes(self):
        for b in self.m.boundaries:
            fn = self.fns[b.function]
            spec = CRASH.get(fn.lang)
            if spec is None or b.category not in IO:
                continue
            rx, where, rule, msg = spec
            if rx.search(b.call.tail if where == "tail" else b.call.snippet):
                self._add(rule, "medium", b.confidence, fn, b.call.line, msg.format(kind=b.kind))


def analyze(m) -> tuple[list[Finding], dict]:
    fx = Effects(m).run()
    return fx.found, fx.summary
