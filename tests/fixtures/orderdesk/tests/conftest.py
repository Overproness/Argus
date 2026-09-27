import json
import os
import re
import sys
import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

import pytest

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)


class FakeUpstream(BaseHTTPRequestHandler):
    def log_message(self, *args) -> None:
        pass

    def _send(self, payload: dict, status: int = 200) -> None:
        body = json.dumps(payload).encode()
        self.send_response(status)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def do_GET(self) -> None:
        if m := re.fullmatch(r"/price/(SKU-\d+)", self.path):
            self._send({"price": 5.0 + int(m.group(1).split("-")[1]) * 0.1})
        elif re.fullmatch(r"/stock/SKU-\d+", self.path):
            self._send({"available": 7})
        elif re.fullmatch(r"/reviews/SKU-\d+", self.path):
            self._send({"reviews": [{"stars": 4, "text": "solid"}]})
        elif self.path == "/feed":
            self._send({"prices": {"SKU-1": 6.1, "SKU-2": 6.2}})
        else:
            self._send({"error": "not found"}, 404)

    def do_POST(self) -> None:
        length = int(self.headers.get("Content-Length") or 0)
        self.rfile.read(length)
        if self.path == "/token":
            self._send({"token": "test-token"})
        elif self.path in ("/reserve", "/events"):
            self._send({"ok": True})
        else:
            self._send({"error": "not found"}, 404)


_server = ThreadingHTTPServer(("127.0.0.1", 0), FakeUpstream)
threading.Thread(target=_server.serve_forever, daemon=True).start()
_base = f"http://127.0.0.1:{_server.server_address[1]}"
for var in ("PRICING_URL", "INVENTORY_URL", "REVIEWS_URL", "EVENTS_URL"):
    os.environ[var] = _base


@pytest.fixture(scope="session")
def client():
    from fastapi.testclient import TestClient

    from shop.api import app

    with TestClient(app) as c:
        yield c
