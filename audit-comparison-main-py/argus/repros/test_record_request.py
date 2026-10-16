"""Reproduction for unbounded-module-container@app.record_request:28.

Module-level `REQUEST_COUNTS` is only ever added to across this file, never popped, removed or cleared. Every request or background cycle that adds to it grows the process's memory a little more, for as long as the process runs. It is never read back by key either; check it is still needed at all. Evict old entries (an LRU/TTL cache, a max size, or a cleanup pass) or move it out of module scope.

Fill in the TODOs below: the trigger (what makes the effect happen here) and, where marked, the call
itself. Everything else is the usual shape for this rule - delete what you don't need, but don't change
the import list or the evidence()/assert shape without a reason.
"""

from auditor.repro.harness import evidence


def test_record_request_grows_without_bound():
    import importlib
    # TODO: import the real module (`m = importlib.import_module("...")`) and call `record_request` N times.
    N = 50
    size_before = size_after = None
    evidence(finding="unbounded-module-container@app.record_request:28", before=size_before, after=size_after, calls=N)
    assert size_after == size_before + N  # nothing was ever evicted
