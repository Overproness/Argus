"""Reproductions for the five findings in app.main.fetch_remote.

The real `fetch_remote` and the real `httpx.AsyncClient` run. The only thing replaced is the transport
(an in-process httpx.MockTransport standing in for the hard-coded https://example.com), injected through
the client's own `transport=` argument so no real network is touched.
"""

import asyncio
from urllib.parse import parse_qs

import httpx
from auditor.repro.harness import count_calls, evidence, refuse_remote, run_concurrently  # noqa: F401


class Peer:
    def __init__(self, status=200, body="remote-body", delay=0.02):
        self.status, self.body, self.delay = status, body, delay
        self.requests: list[httpx.Request] = []
        self.clients_built = 0

    def install(self, monkeypatch):
        peer = self
        original_init = httpx.AsyncClient.__init__

        async def handler(request: httpx.Request) -> httpx.Response:
            peer.requests.append(request)
            await asyncio.sleep(peer.delay)  # a real upstream round trip yields to the loop
            return httpx.Response(peer.status, text=peer.body)

        def init(self_, *a, **k):
            peer.clients_built += 1
            k.setdefault("transport", httpx.MockTransport(handler))
            return original_init(self_, *a, **k)

        monkeypatch.setattr(httpx.AsyncClient, "__init__", init)


def _reset(monkeypatch, main):
    monkeypatch.setattr(main, "CACHE", {})
    monkeypatch.setattr(main, "REQUEST_COUNTS", {})


def test_fetch_remote_loses_updates(monkeypatch):
    import app.main as main

    _reset(monkeypatch, main)
    peer = Peer()
    peer.install(monkeypatch)
    N = 50
    with refuse_remote():
        results = asyncio.run(run_concurrently(main.fetch_remote, N, "same-key"))
    uncached = sum(1 for r in results if isinstance(r, dict) and r["cached"] is False)
    evidence(finding="race-across-await@app.fetch_remote:74", upstream_calls=len(peer.requests),
             uncached_responses=uncached, expected=1, cache_entries=len(main.CACHE))
    # check-then-act: the cache check happens before the await, the write after, so every concurrent
    # request for the same cold key goes upstream instead of one.
    assert len(peer.requests) > 1


def test_fetch_remote_client_per_call(monkeypatch):
    import app.main as main

    _reset(monkeypatch, main)
    peer = Peer(delay=0)
    peer.install(monkeypatch)
    N = 20

    async def go():
        for i in range(N):
            await main.fetch_remote(f"k{i}")

    with refuse_remote():
        asyncio.run(go())
    evidence(finding="client-per-call@app.fetch_remote:78", clients_built=peer.clients_built, calls=N)
    assert peer.clients_built == N


def test_fetch_remote_unsanitized_query(monkeypatch):
    import app.main as main

    _reset(monkeypatch, main)
    peer = Peer(delay=0)
    peer.install(monkeypatch)
    with refuse_remote():
        asyncio.run(main.fetch_remote("abc&evil=1"))
    req = peer.requests[0]
    params = parse_qs(req.url.query.decode())
    evidence(finding="unsanitized-url-query@app.fetch_remote:79", raw_url=str(req.url), params=params)
    assert "evil" in params  # the key's '&' became a second query parameter


def test_fetch_remote_unchecked_response(monkeypatch):
    import app.main as main

    _reset(monkeypatch, main)
    peer = Peer(status=500, body="not json: upstream exploded", delay=0)
    peer.install(monkeypatch)
    with refuse_remote():
        first = asyncio.run(main.fetch_remote("k"))
        second = asyncio.run(main.fetch_remote("k"))
    evidence(finding="unchecked-response@app.fetch_remote:80", first=first, second=second,
             upstream_calls=len(peer.requests), upstream_status=500)
    # a 500 is returned as data and then served from cache for 60 s; the peer is not asked again
    assert first["value"] == "not json: upstream exploded"
    assert second["cached"] is True and len(peer.requests) == 1


def test_fetch_remote_cache_unbounded(monkeypatch):
    import app.main as main
    import time as _time

    _reset(monkeypatch, main)
    peer = Peer(delay=0)
    peer.install(monkeypatch)
    N = 500

    async def go():
        for i in range(N):
            await main.fetch_remote(f"key-{i}")

    with refuse_remote():
        asyncio.run(go())
        before = len(main.CACHE)
        # one hour later every entry is expired, but nothing ever removes them
        real = _time.time
        monkeypatch.setattr(main.time, "time", lambda: real() + 3600)
        asyncio.run(main.fetch_remote("one-more"))
    expired = sum(1 for exp, _ in main.CACHE.values() if exp < real() + 3600)
    evidence(finding="unbounded-module-container@app.fetch_remote:81", entries_after_n=before, n=N,
             entries_after_expiry=len(main.CACHE), expired_still_held=expired)
    assert before == N and len(main.CACHE) == N + 1
