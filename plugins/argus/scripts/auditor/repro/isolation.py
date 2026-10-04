"""pytest plugin loaded by the runner (`-p auditor.repro.isolation`): undo what one test did to the process.

Each reproduction file already runs in its own process. Within a file, a test that
`chdir`s, pushes onto `sys.path` or patches `socket.socket.connect` and fails before
cleaning up would change what the next test sees; this restores all three after
every test, so a test's outcome depends only on the test.

It also prints which `auditor` package this process actually imported (`pytest_configure`): a stale
install on PYTHONPATH ahead of the one `repro` set up imports silently, and a repro written against a
helper that doesn't exist there fails confusingly far from the real cause. The runner always invokes
pytest through this plugin, so hand-running `pytest` directly skips this check — see the warning in
`auditor_cli.py repro --help` and `agents/investigator.md`.
"""
from __future__ import annotations

import os
import socket
import sys

import pytest


def pytest_configure(config):
    import auditor
    print(f"@@argus-auditor {auditor.__file__}", file=sys.stderr)


@pytest.fixture(autouse=True)
def _argus_restore_process_state():
    cwd = os.getcwd()
    path = list(sys.path)
    connect = socket.socket.connect
    try:
        yield
    finally:
        if os.getcwd() != cwd:
            os.chdir(cwd)
        sys.path[:] = path
        socket.socket.connect = connect
