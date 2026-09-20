"""Make the MCP server start on a machine where the system python has none of Argus's dependencies.

If the imports work, nothing happens. If not, a private virtual environment under ~/.cache/argus (or
$ARGUS_HOME) is created once, requirements.txt is installed into it, and this process is replaced by the
same script running under that environment's python.
"""
from __future__ import annotations

import importlib.util
import os
import subprocess
import sys
import venv
from pathlib import Path

REQS = Path(__file__).resolve().parent.parent / "requirements.txt"
NEEDED = ("tree_sitter", "tree_sitter_language_pack", "mcp")


def _missing() -> bool:
    return any(importlib.util.find_spec(m) is None for m in NEEDED)


def ensure_dependencies() -> None:
    if not _missing() or os.environ.get("ARGUS_BOOTSTRAPPED"):
        return
    home = Path(os.environ.get("ARGUS_HOME") or Path.home() / ".cache" / "argus") / "venv"
    py = home / ("Scripts/python.exe" if os.name == "nt" else "bin/python")
    if not py.exists():
        print(f"argus: installing dependencies into {home} (first run)", file=sys.stderr)
        venv.EnvBuilder(with_pip=True, clear=False).create(home)
        subprocess.run([str(py), "-m", "pip", "install", "-q", "-r", str(REQS)], check=True, stdout=sys.stderr)
    env = {**os.environ, "ARGUS_BOOTSTRAPPED": "1"}
    os.execve(str(py), [str(py), *sys.argv], env)
