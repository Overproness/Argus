"""Run an application contract against an exported source tree.

Exit 0: contract holds. Exit 1: contract violated. Exit 2: setup/unexpected
error. The same driver runs on both sides of every pair, in separate processes.
"""
import argparse
import asyncio
import json
import sys
import tempfile
import threading
import time
import traceback
from contextlib import contextmanager
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path


@contextmanager
def upstream(delay=0, status=200):
    calls = []

    class Handler(BaseHTTPRequestHandler):
        def respond(self):
            calls.append(time.monotonic())
            self.rfile.read(int(self.headers.get("Content-Length", "0")))
            time.sleep(delay)
            self.send_response(status)
            self.end_headers()
            try:
                self.wfile.write(b'{"available": 4, "event": "accepted"}')
            except (BrokenPipeError, ConnectionResetError, ConnectionAbortedError):
                pass

        do_GET = do_POST = respond

        def log_message(self, *_):
            pass

    with ThreadingHTTPServer(("127.0.0.1", 0), Handler) as server:
        server.daemon_threads = True
        thread = threading.Thread(target=server.serve_forever, kwargs={"poll_interval": 0.01}, daemon=True)
        thread.start()
        try:
            yield f"http://127.0.0.1:{server.server_port}", calls
        finally:
            server.shutdown()
            thread.join(timeout=2)


def check(name):
    if name == "inventory":
        from relay.gateway import InventoryClient
        with upstream(delay=0.15) as (endpoint, calls):
            async def run():
                ticks, stop = [], asyncio.Event()

                async def heartbeat():
                    while not stop.is_set():
                        ticks.append(time.monotonic())
                        await asyncio.sleep(0.002)

                sibling = asyncio.create_task(heartbeat())
                await asyncio.sleep(0.01)
                try:
                    result = await InventoryClient(endpoint, timeout=0.4).fetch_inventory("parcel")
                    assert result["available"] == 4
                    await asyncio.sleep(0.01)
                finally:
                    stop.set()
                    await sibling
                return max(b - a for a, b in zip(ticks, ticks[1:]))
            lag = asyncio.run(run())
            return lag < 0.075, dict(max_tick_gap_s=lag, upstream_calls=len(calls))
    if name == "event":
        from relay.gateway import EventClient
        with upstream(delay=0.3) as (endpoint, calls):
            start = time.monotonic()
            timed_out = False
            try:
                asyncio.run(EventClient(endpoint, timeout=0.08).get_event("parcel"))
            except TimeoutError:
                timed_out = True
            elapsed = time.monotonic() - start
            return timed_out and elapsed < 0.19, dict(timed_out=timed_out, elapsed_s=elapsed, upstream_calls=len(calls))
    if name == "claims":
        from relay import claims

        async def run():
            claims.NEXT_TICKET = 0
            claims.CLAIM_LOCK = asyncio.Lock()

            async def authorize():
                await asyncio.sleep(0.002)

            ids = await asyncio.gather(*[claims.next_ticket(authorize) for _ in range(24)])
            return ids
        ids = asyncio.run(run())
        return len(set(ids)) == 24, dict(requests=24, unique_tickets=len(set(ids)))
    if name == "receipts":
        from relay.receipts import ReceiptStore
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "receipts.sqlite"
            ReceiptStore(path).save(7, "delivered")
            persisted = ReceiptStore(path).get(7)
            return persisted == "delivered", dict(persisted=persisted)
    if name == "retention":
        from relay.models import Job
        from relay.retention import drop_expired
        jobs = [Job("a", 0), Job("b", 0), Job("c", 0), Job("active", None)]
        drop_expired(jobs, now=120, ttl=60)
        remaining = [j.key for j in jobs]
        return remaining == ["active"], dict(remaining=remaining)
    if name == "replay":
        from relay.replay import ReplayClient
        with upstream(status=503) as (endpoint, calls):
            async def run():
                try:
                    await asyncio.wait_for(ReplayClient(endpoint).push(b"parcel"), timeout=0.3)
                except TimeoutError:
                    return "caller_deadline"
                except RuntimeError:
                    return "retry_budget"
                return "accepted"
            stopped_by = asyncio.run(run())
            return stopped_by == "retry_budget" and len(calls) == 3, dict(stopped_by=stopped_by, upstream_calls=len(calls))
    if name == "dispatch":
        from relay.dispatch import Dispatcher

        class Adapter:
            active = peak = 0

            async def push(self, value):
                self.active += 1
                self.peak = max(self.peak, self.active)
                try:
                    await asyncio.sleep(0.008)
                    return value
                finally:
                    self.active -= 1

        adapter = Adapter()
        output = asyncio.run(Dispatcher(adapter, parallelism=4).deliver_many(list(range(24))))
        assert output == list(range(24))
        return adapter.peak <= 4, dict(deliveries=24, peak_active=adapter.peak)
    if name == "health":
        from relay.health import HealthMonitor
        start = time.monotonic()
        timed_out = False
        try:
            asyncio.run(HealthMonitor(delay=0.15, deadline=0.03).check())
        except TimeoutError:
            timed_out = True
        elapsed = time.monotonic() - start
        return timed_out and elapsed < 0.09, dict(timed_out=timed_out, elapsed_s=elapsed)
    if name == "formatter":
        from relay.formatter import BatchFormatter
        formatter = BatchFormatter()
        try:
            formatter.render({"unsupported": object()})
        except TypeError:
            pass
        else:
            raise AssertionError("malformed input did not raise TypeError")
        available = formatter._lock.acquire(timeout=0.03)
        if available:
            formatter._lock.release()
        return available, dict(lock_available_after_error=available)
    if name == "events":
        from relay.events import EventBuffer
        buffer = EventBuffer(capacity=16)
        accepted = sum(buffer.offer(i) for i in range(100))
        return accepted == 16 and buffer.queue.qsize() == 16, dict(offers=100, accepted=accepted, backlog=buffer.queue.qsize())
    raise ValueError(f"Unknown contract {name}")


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--repo", type=Path, required=True)
    ap.add_argument("--scenario", required=True)
    args = ap.parse_args()
    root = args.repo.resolve()
    sys.path.insert(0, str(root))
    try:
        import relay
        if Path(relay.__file__).resolve().parent != root / "relay":
            raise RuntimeError("Imported a different relay package")
        satisfied, metrics = check(args.scenario)
        print(json.dumps(dict(satisfied=satisfied, metrics=metrics)))
        return 0 if satisfied else 1
    except Exception:
        print(json.dumps(dict(error=traceback.format_exc())))
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
