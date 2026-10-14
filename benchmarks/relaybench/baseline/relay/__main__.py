"""Run one complete delivery against a loopback upstream."""
import asyncio
import json
import tempfile
import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from .service import RelayService


class Upstream(BaseHTTPRequestHandler):
    def do_GET(self):
        body = json.dumps({"available": 10, "event": "accepted"}).encode()
        self.send_response(200)
        self.end_headers()
        self.wfile.write(body)

    def do_POST(self):
        self.rfile.read(int(self.headers.get("Content-Length", "0")))
        self.send_response(200)
        self.end_headers()
        self.wfile.write(b"accepted")

    def log_message(self, *_):
        pass


def main():
    with ThreadingHTTPServer(("127.0.0.1", 0), Upstream) as server:
        server.daemon_threads = True
        worker = threading.Thread(target=server.serve_forever, daemon=True)
        worker.start()
        try:
            with tempfile.TemporaryDirectory() as directory:
                service = RelayService(f"http://127.0.0.1:{server.server_port}", Path(directory) / "receipts.sqlite")
                result = asyncio.run(service.submit("parcel", {"destination": "local"}))
                print(json.dumps(result))
        finally:
            server.shutdown()
            worker.join()


if __name__ == "__main__":
    main()
