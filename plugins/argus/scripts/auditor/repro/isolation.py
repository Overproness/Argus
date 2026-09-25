"""pytest plugin loaded by the runner (`-p auditor.repro.isolation`): undo what one test did to the process.

Each reproduction file already runs in its own process. Within a file, a test that
`chdir`s, pushes onto `sys.path` or patches `socket.socket.connect` and fails before
cleaning up would change what the next test sees; this restores all three after
every test, so a test's outcome depends only on the test.
"""
from __future__ import annotations

import os
import socket
import sys

import pytest


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
