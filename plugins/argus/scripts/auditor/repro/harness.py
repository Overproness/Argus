"""Building blocks for reproduction tests written by the investigator.

Every helper reports what it measured through `evidence(...)`, which the runner
collects from the test's captured stdout. A reproduction proves a finding by
making the predicted effect happen under controlled conditions:

  latency(2.0)          every outbound socket connect waits 2 s (a slow peer)
  hang()                connects never complete (a dead peer)
  fail_connect()        every connect fails at once (an outage), to drive retry logic
  count_connects()      counts connect attempts and the gaps between them (retries, backoff)
  loop_monitor()        measures how long the asyncio loop went unresponsive
  call_with_deadline()  runs a blocking call in a thread and gives up after N s
  scaling()             times a function at several input sizes and fits O(n^k)
  count_calls()         counts calls to a function (N+1 checks)
"""
from __future__ import annotations

import asyncio
import json
import socket
import threading
import time
from contextlib import contextmanager
from dataclasses import dataclass, field

from ..trace.fit import Fit, fit_power

EVIDENCE_PREFIX = "@@evidence "
LOCAL_HOSTS = ("127.0.0.1", "::1", "localhost", "0.0.0.0")


def evidence(**facts) -> None:
    """Record a measured fact. Collected by the runner; keep values JSON-serializable."""
    print(EVIDENCE_PREFIX + json.dumps(facts, default=str), flush=True)


# --- network conditions --------------------------------------------------------

def _host_of(addr) -> str:
    return addr[0] if isinstance(addr, tuple) else str(addr)


@contextmanager
def latency(seconds: float, hosts: tuple[str, ...] | None = None):
    """Delay every socket connect (to `hosts`, or to all hosts) by `seconds`."""
    original = socket.socket.connect

    def slow_connect(self, addr):
        if hosts is None or _host_of(addr) in hosts:
            time.sleep(seconds)
        return original(self, addr)

    socket.socket.connect = slow_connect
    try:
        yield
    finally:
        socket.socket.connect = original


def hang(hosts: tuple[str, ...] | None = None):
    """A peer that never answers. Pair with call_with_deadline or loop_monitor."""
    return latency(3600, hosts)


@contextmanager
def fail_connect(hosts: tuple[str, ...] | None = None, error: type[OSError] = ConnectionRefusedError):
    """Every connect (to `hosts`, or to all hosts) fails immediately: an outage."""
    original = socket.socket.connect

    def failing(self, addr):
        if hosts is None or _host_of(addr) in hosts:
            raise error(f"simulated outage: connect to {addr} refused")
        return original(self, addr)

    socket.socket.connect = failing
    try:
        yield
    finally:
        socket.socket.connect = original


@contextmanager
def count_connects(hosts: tuple[str, ...] | None = None):
    """Count connect attempts inside the block. Yields {'connects', 'gaps_s'}; combine with fail_connect()
    (enter fail_connect first) to see how many times retry logic tries and how long it waits between tries."""
    original = socket.socket.connect
    box: dict = {"connects": 0, "gaps_s": []}
    stamps: list[float] = []

    def counting(self, addr):
        if hosts is None or _host_of(addr) in hosts:
            now = time.perf_counter()
            if stamps:
                box["gaps_s"].append(round(now - stamps[-1], 4))
            stamps.append(now)
            box["connects"] += 1
        return original(self, addr)

    socket.socket.connect = counting
    try:
        yield box
    finally:
        socket.socket.connect = original
        gaps = box["gaps_s"]
        evidence(kind="connects", connects=box["connects"],
                 min_gap_s=min(gaps) if gaps else None, max_gap_s=max(gaps) if gaps else None)


@contextmanager
def refuse_remote():
    """Fail any connect that is not loopback. Use it in every reproduction."""
    original = socket.socket.connect

    def guarded(self, addr):
        if _host_of(addr) not in LOCAL_HOSTS:
            raise ConnectionRefusedError(f"reproduction tried to reach {addr}; only loopback is allowed")
        return original(self, addr)

    socket.socket.connect = guarded
    try:
        yield
    finally:
        socket.socket.connect = original


# --- event loop responsiveness --------------------------------------------------

@dataclass
class LoopMonitor:
    period: float
    max_lag: float = 0.0
    lags: list[float] = field(default_factory=list)
    _task: asyncio.Task | None = None

    async def _run(self):
        loop = asyncio.get_running_loop()
        while True:
            t0 = loop.time()
            await asyncio.sleep(self.period)
            lag = loop.time() - t0 - self.period
            self.lags.append(lag)
            if lag > self.max_lag:
                self.max_lag = lag

    async def __aenter__(self):
        self._task = asyncio.ensure_future(self._run())
        await asyncio.sleep(0)
        return self

    async def __aexit__(self, *exc):
        self._task.cancel()
        try:
            await self._task
        except asyncio.CancelledError:
            pass
        evidence(kind="loop_lag", max_lag_s=round(self.max_lag, 4), ticks=len(self.lags), period_s=self.period)


def loop_monitor(period: float = 0.01) -> LoopMonitor:
    """`async with loop_monitor() as m:` then read `m.max_lag`: the longest the loop was frozen."""
    return LoopMonitor(period)


# --- blocking calls -------------------------------------------------------------

def call_with_deadline(fn, deadline: float, *args, **kwargs) -> dict:
    """Run a blocking call in a thread. Returns {returned, elapsed_s, result|error}."""
    box: dict = {}

    def target():
        t0 = time.perf_counter()
        try:
            box["result"] = fn(*args, **kwargs)
        except BaseException as e:  # noqa: BLE001 - report whatever the call raised
            box["error"] = f"{type(e).__name__}: {e}"
        box["elapsed_s"] = round(time.perf_counter() - t0, 4)

    t = threading.Thread(target=target, daemon=True)
    t0 = time.perf_counter()
    t.start()
    t.join(deadline)
    out = {"returned": not t.is_alive(), "elapsed_s": round(time.perf_counter() - t0, 4), "deadline_s": deadline}
    out.update(box)
    evidence(kind="deadline", fn=getattr(fn, "__qualname__", str(fn)), **out)
    return out


# --- scaling --------------------------------------------------------------------

def scaling(fn, sizes, make_input, repeat: int = 3, arg: str = "n") -> Fit | None:
    """Time fn(make_input(n)) for each n and fit duration ~ n^k."""
    points = []
    for n in sizes:
        for _ in range(repeat):
            data = make_input(n)
            t0 = time.perf_counter()
            fn(data)
            points.append((float(n), time.perf_counter() - t0))
    fit = fit_power(points, arg)
    evidence(kind="scaling", fn=getattr(fn, "__qualname__", str(fn)), sizes=list(sizes),
             fit=fit.__dict__ if fit else None)
    return fit


# --- call counting --------------------------------------------------------------

@contextmanager
def count_calls(module, name: str):
    """Count calls to module.<name> inside the block; yields a dict with 'calls'."""
    original = getattr(module, name)
    box = {"calls": 0}

    def counting(*a, **k):
        box["calls"] += 1
        return original(*a, **k)

    setattr(module, name, counting)
    try:
        yield box
    finally:
        setattr(module, name, original)
        evidence(kind="call_count", fn=f"{module.__name__}.{name}", calls=box["calls"])


def timed(fn, *args, **kwargs) -> tuple:
    t0 = time.perf_counter()
    result = fn(*args, **kwargs)
    return result, time.perf_counter() - t0
