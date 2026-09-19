"""A stand-in for a remote dependency, with faults on demand, for programs in any language.

The program under test is pointed at the fault server instead of the real
dependency (a base-URL environment variable, config value or CLI flag). The
server either answers HTTP itself (a mock API) or forwards TCP to a local
upstream (a proxy in front of a local database or service), and injects faults:

  latency=s      wait s seconds before answering or forwarding (a slow peer)
  hang=True      accept the connection, then never answer (a dead peer)
  reset=True     close every connection at once with a TCP reset (an outage)
  fail_first=n   reset the first n connections, then behave (a flaky peer, for retries)

Every connection is recorded with its time, peer and what the server did, so
retries, backoff gaps and fan-out can be counted from outside the program.
Faults can be changed while the server runs (`set(...)`), e.g. healthy first,
then slow.
"""
from __future__ import annotations

import argparse
import json
import socket
import socketserver
import struct
import sys
import threading
import time
from contextlib import contextmanager
from dataclasses import asdict, dataclass

from .harness import evidence

REASONS = {200: "OK", 201: "Created", 204: "No Content", 400: "Bad Request", 404: "Not Found",
           429: "Too Many Requests", 500: "Internal Server Error", 502: "Bad Gateway", 503: "Service Unavailable"}


@dataclass
class Faults:
    latency: float = 0.0
    hang: bool = False
    reset: bool = False
    fail_first: int = 0


class _Server(socketserver.ThreadingTCPServer):
    daemon_threads = True
    allow_reuse_address = True


class FaultServer:
    def __init__(self, status: int = 200, body: bytes | str = b"{}", content_type: str = "application/json",
                 upstream: tuple[str, int] | None = None, host: str = "127.0.0.1", port: int = 0, **faults):
        self.faults = Faults(**faults)
        self.status = status
        self.body = body.encode() if isinstance(body, str) else body
        self.content_type = content_type
        self.upstream = upstream
        self.connections: list[dict] = []
        self._failed = 0
        self._lock = threading.Lock()
        self._stop = threading.Event()
        self._t0 = time.perf_counter()
        owner = self

        class Handler(socketserver.BaseRequestHandler):
            def handle(self):
                owner._handle(self.request, self.client_address)

        self._server = _Server((host, port), Handler)
        self.host, self.port = self._server.server_address[:2]
        self._thread: threading.Thread | None = None

    # --- control ------------------------------------------------------------------
    @property
    def url(self) -> str:
        return f"http://{self.host}:{self.port}"

    @property
    def address(self) -> str:
        return f"{self.host}:{self.port}"

    def set(self, **faults) -> "FaultServer":
        """Change faults while running; setting fail_first restarts its count."""
        with self._lock:
            for k, v in faults.items():
                setattr(self.faults, k, v)
            if "fail_first" in faults:
                self._failed = 0
        return self

    def start(self) -> "FaultServer":
        self._thread = threading.Thread(target=self._server.serve_forever, kwargs={"poll_interval": 0.05},
                                        daemon=True)
        self._thread.start()
        return self

    def stop(self):
        self._stop.set()
        self._server.shutdown()
        self._server.server_close()

    # --- per connection -----------------------------------------------------------------
    def _handle(self, conn: socket.socket, peer):
        with self._lock:
            f = Faults(**asdict(self.faults))
            if f.fail_first and self._failed < f.fail_first:
                self._failed += 1
                action = "reset"
            elif f.reset:
                action = "reset"
            elif f.hang:
                action = "hang"
            else:
                action = "proxy" if self.upstream else "respond"
            rec = {"t_s": round(time.perf_counter() - self._t0, 4), "peer": f"{peer[0]}:{peer[1]}",
                   "action": action, "latency_s": f.latency}
            self.connections.append(rec)
        if action == "reset":
            self._rst(conn)
            return
        if action == "hang":
            self._stop.wait()  # hold the socket open until the server stops
            return
        if f.latency and self._stop.wait(f.latency):
            return
        if self.upstream:
            self._proxy(conn)
        else:
            self._respond(conn, rec)

    @staticmethod
    def _rst(conn: socket.socket):
        # SO_LINGER with a zero timeout turns close() into a TCP RST.
        fmt = "hh" if sys.platform == "win32" else "ii"
        try:
            conn.setsockopt(socket.SOL_SOCKET, socket.SO_LINGER, struct.pack(fmt, 1, 0))
        except OSError:
            pass
        conn.close()

    def _respond(self, conn: socket.socket, rec: dict):
        conn.settimeout(10)
        data = b""
        try:
            while b"\r\n\r\n" not in data and len(data) < 65536:
                chunk = conn.recv(4096)
                if not chunk:
                    break
                data += chunk
            head = data.split(b"\r\n", 1)[0].decode("latin-1", "replace")
            rec["request"] = head[:200]
            reason = REASONS.get(self.status, "Status")
            conn.sendall(
                f"HTTP/1.1 {self.status} {reason}\r\nContent-Type: {self.content_type}\r\n"
                f"Content-Length: {len(self.body)}\r\nConnection: close\r\n\r\n".encode() + self.body)
        except OSError:
            pass
        finally:
            try:
                conn.shutdown(socket.SHUT_RDWR)
            except OSError:
                pass
            conn.close()

    def _proxy(self, conn: socket.socket):
        try:
            up = socket.create_connection(self.upstream, timeout=10)
        except OSError:
            self._rst(conn)
            return
        up.settimeout(None)

        def pump(src, dst):
            try:
                while chunk := src.recv(65536):
                    dst.sendall(chunk)
            except OSError:
                pass
            finally:
                try:
                    dst.shutdown(socket.SHUT_WR)
                except OSError:
                    pass

        t = threading.Thread(target=pump, args=(up, conn), daemon=True)
        t.start()
        pump(conn, up)
        t.join(30)
        up.close()
        conn.close()

    # --- results --------------------------------------------------------------------------
    def summary(self) -> dict:
        with self._lock:
            conns = list(self.connections)
        times = [c["t_s"] for c in conns]
        gaps = [round(b - a, 4) for a, b in zip(times, times[1:])]
        actions: dict[str, int] = {}
        for c in conns:
            actions[c["action"]] = actions.get(c["action"], 0) + 1
        return {"connections": len(conns), "actions": actions, "gaps_s": gaps[:50],
                "min_gap_s": min(gaps) if gaps else None, "max_gap_s": max(gaps) if gaps else None,
                "requests": [c.get("request") for c in conns if c.get("request")][:20],
                "faults": asdict(self.faults)}


@contextmanager
def fault_server(**kw):
    """`with fault_server(hang=True) as srv:` point the program at srv.url (HTTP) or srv.address (TCP).
    On exit the connection summary is recorded as evidence."""
    srv = FaultServer(**kw).start()
    try:
        yield srv
    finally:
        srv.stop()
        evidence(kind="fault_server", **srv.summary())


def main(argv: list[str] | None = None) -> int:
    """`auditor_cli.py fault-server`: run one from a shell, for black-box runs driven by hand."""
    ap = argparse.ArgumentParser(prog="auditor fault-server")
    ap.add_argument("--port", type=int, default=0)
    ap.add_argument("--latency", type=float, default=0.0, help="seconds before answering/forwarding")
    ap.add_argument("--hang", action="store_true", help="accept, never answer")
    ap.add_argument("--reset", action="store_true", help="reset every connection")
    ap.add_argument("--fail-first", type=int, default=0, help="reset the first N connections")
    ap.add_argument("--status", type=int, default=200)
    ap.add_argument("--body", default="{}")
    ap.add_argument("--content-type", default="application/json")
    ap.add_argument("--proxy", help="forward to HOST:PORT instead of answering HTTP")
    ap.add_argument("--duration", type=float, help="stop after this many seconds (default: until Ctrl-C)")
    ap.add_argument("--record", help="append one JSON line per connection to this file")
    a = ap.parse_args(argv)
    upstream = None
    if a.proxy:
        host, _, port = a.proxy.rpartition(":")
        upstream = (host or "127.0.0.1", int(port))
    srv = FaultServer(status=a.status, body=a.body, content_type=a.content_type, upstream=upstream, port=a.port,
                      latency=a.latency, hang=a.hang, reset=a.reset, fail_first=a.fail_first).start()
    print(f"listening {srv.url}" if not upstream else f"listening {srv.address} -> {a.proxy}", flush=True)
    written = 0
    t_end = time.monotonic() + a.duration if a.duration else None
    try:
        while t_end is None or time.monotonic() < t_end:
            time.sleep(0.2)
            if a.record and len(srv.connections) > written:
                with open(a.record, "a", encoding="utf8") as fh:
                    for c in srv.connections[written:]:
                        fh.write(json.dumps(c) + "\n")
                written = len(srv.connections)
    except KeyboardInterrupt:
        pass
    finally:
        srv.stop()
        print(json.dumps(srv.summary()), flush=True)
    return 0
