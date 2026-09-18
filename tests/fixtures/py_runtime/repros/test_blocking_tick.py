"""Reproduction: blocking-in-async at app/service.py:30 (tick -> compute -> fetch_quote).

Hypothesis: a slow quote server freezes the asyncio loop for the whole fetch,
because fetch_quote uses blocking urllib on the loop thread.
Trigger: 0.3 s connect latency. Expected: loop lag >= 0.3 s per fetch.
"""
import asyncio
import threading
from http.server import BaseHTTPRequestHandler, HTTPServer

from auditor.repro.harness import evidence, latency, loop_monitor, refuse_remote

from app import service

FINDING = "blocking-in-async@app.service.tick:30"


class Quick(BaseHTTPRequestHandler):
    def do_GET(self):
        self.send_response(200)
        self.end_headers()
        self.wfile.write(b"1")

    def log_message(self, *a):
        pass


def test_slow_peer_freezes_loop():
    srv = HTTPServer(("127.0.0.1", 0), Quick)
    threading.Thread(target=srv.serve_forever, daemon=True).start()
    service.BASE = f"http://127.0.0.1:{srv.server_port}"

    async def scenario():
        async with loop_monitor(period=0.01) as mon:
            with latency(0.3):
                await service.tick(["BTC"])
        return mon.max_lag

    with refuse_remote():
        lag = asyncio.run(scenario())
    srv.shutdown()
    evidence(finding=FINDING, injected_latency_s=0.3, loop_max_lag_s=round(lag, 3))
    assert lag >= 0.25, f"loop stayed responsive (max lag {lag:.3f}s): finding not reproduced"
