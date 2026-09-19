"""Trace store: one SQLite file per traced process, merged on read.

A `call` is one activation of a repo function. Durations are wall-clock seconds:
`dur` is inclusive, `self_dur` excludes traced children (so time spent in
untraced library code lands on the repo function that called it). Coroutines run
in slices between awaits; `max_self_slice` is the longest uninterrupted one.
"""
from __future__ import annotations

import json
import sqlite3
from dataclasses import dataclass
from pathlib import Path

SCHEMA = """
CREATE TABLE IF NOT EXISTS meta (key TEXT PRIMARY KEY, value TEXT);
CREATE TABLE IF NOT EXISTS calls (
    id INTEGER, parent INTEGER, thread INTEGER,
    file TEXT, line INTEGER, qualname TEXT, is_coro INTEGER,
    start REAL, dur REAL, self_dur REAL, slices INTEGER, max_self_slice REAL,
    shapes TEXT
);
CREATE TABLE IF NOT EXISTS stalls (
    call_id INTEGER, file TEXT, line INTEGER, qualname TEXT, dur REAL, at REAL, stack TEXT
);
"""
FLUSH_EVERY = 5000


class TraceWriter:
    def __init__(self, path: Path, meta: dict[str, str]):
        path.parent.mkdir(parents=True, exist_ok=True)
        self.conn = sqlite3.connect(str(path), isolation_level=None, check_same_thread=False)
        self.conn.executescript("PRAGMA journal_mode=OFF; PRAGMA synchronous=OFF;" + SCHEMA)
        self.conn.executemany("INSERT OR REPLACE INTO meta VALUES (?, ?)", list(meta.items()))
        self._calls: list[tuple] = []
        self._stalls: list[tuple] = []

    def add_call(self, row: tuple):
        self._calls.append(row)
        if len(self._calls) >= FLUSH_EVERY:
            self.flush()

    def add_stall(self, row: tuple):
        self._stalls.append(row)

    def flush(self):
        if self._calls:
            self.conn.executemany("INSERT INTO calls VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?)", self._calls)
            self._calls.clear()
        if self._stalls:
            self.conn.executemany("INSERT INTO stalls VALUES (?,?,?,?,?,?,?)", self._stalls)
            self._stalls.clear()

    def close(self):
        self.flush()
        self.conn.close()


@dataclass(frozen=True)
class CallRec:
    pid: int
    id: int
    parent: int
    thread: int
    file: str
    line: int
    qualname: str
    is_coro: bool
    start: float
    dur: float
    self_dur: float
    slices: int
    max_self_slice: float
    shapes: dict[str, float]

    @property
    def key(self) -> tuple[str, int]:
        return self.file, self.line


@dataclass(frozen=True)
class StallRec:
    pid: int
    call_id: int
    file: str
    line: int
    qualname: str
    dur: float
    at: float
    stack: list[tuple[str, int, str]]


@dataclass
class Trace:
    runs: list[dict[str, str]]
    calls: list[CallRec]
    stalls: list[StallRec]

    def by_key(self) -> dict[tuple[str, int], list[CallRec]]:
        out: dict[tuple[str, int], list[CallRec]] = {}
        for c in self.calls:
            out.setdefault(c.key, []).append(c)
        return out


def load(trace_dir: Path) -> Trace:
    runs, calls, stalls = [], [], []
    for db in sorted(trace_dir.glob("*.db")):
        conn = sqlite3.connect(f"file:{db}?mode=ro", uri=True)
        try:
            meta = dict(conn.execute("SELECT key, value FROM meta"))
            pid = int(meta.get("pid", 0))
            runs.append(meta)
            for row in conn.execute("SELECT * FROM calls"):
                calls.append(CallRec(pid, *row[:6], bool(row[6]), *row[7:12], json.loads(row[12] or "{}")))
            for row in conn.execute("SELECT * FROM stalls"):
                stalls.append(StallRec(pid, *row[:6], [tuple(x) for x in json.loads(row[6])]))
        finally:
            conn.close()
    return Trace(runs, calls, stalls)
