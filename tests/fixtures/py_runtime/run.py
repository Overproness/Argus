"""Drives app.service against a local HTTP server that answers slowly."""
import asyncio
import threading
import time
from http.server import BaseHTTPRequestHandler, HTTPServer

from app import service


class Slow(BaseHTTPRequestHandler):
    def do_GET(self):
        time.sleep(0.15)
        self.send_response(200)
        self.end_headers()
        self.wfile.write(b"42")

    def log_message(self, *a):
        pass


async def main():
    for _ in range(2):
        await service.tick(["BTC", "SOL"])
    await service.warm(["A", "B", "C"])
    for n in (10, 30, 100, 300, 600):
        service.matrix(list(range(n)))
    service.depth(25)


if __name__ == "__main__":
    srv = HTTPServer(("127.0.0.1", 0), Slow)
    threading.Thread(target=srv.serve_forever, daemon=True).start()
    service.BASE = f"http://127.0.0.1:{srv.server_port}"
    asyncio.run(main())
    srv.shutdown()
