"""Identify the plugin payload, including the skills that drive an investigation.

Capture an identity when the process starts: an MCP server can outlive an update
to its files. A fresh CLI process alone cannot identify that running server.
"""
from __future__ import annotations

import hashlib
import json
import os
import sys
from pathlib import Path


PLUGIN_ROOT = Path(__file__).resolve().parents[2]
PAYLOAD_DIRS = (".claude-plugin", "agents", "hooks", "scripts", "skills")
PAYLOAD_FILES = (".mcp.json", "requirements.txt")
SKIP_DIRS = {"__pycache__", ".audit", ".venv", "venv", "node_modules"}


def fingerprint(root: Path) -> str:
    """Hash shipped files by relative path and bytes, independent of install path."""
    files = [root / name for name in PAYLOAD_FILES if (root / name).is_file()]
    for name in PAYLOAD_DIRS:
        files.extend(p for p in (root / name).rglob("*")
                     if p.is_file() and not SKIP_DIRS.intersection(p.relative_to(root).parts)
                     and p.suffix not in {".pyc", ".pyo"})
    hashes = {p.relative_to(root).as_posix(): hashlib.sha256(p.read_bytes()).hexdigest()
              for p in files}
    return hashlib.sha256(json.dumps(hashes, sort_keys=True).encode()).hexdigest()


LOADED_SHA256 = fingerprint(PLUGIN_ROOT)


def runtime_identity() -> dict:
    """Read-only status of this process; no network, credentials or Git required."""
    current = fingerprint(PLUGIN_ROOT)
    manifest = json.loads((PLUGIN_ROOT / ".claude-plugin/plugin.json").read_text(encoding="utf8"))
    return {
        "plugin_root": str(PLUGIN_ROOT),
        "declared_version": manifest.get("version"),
        "plugin_sha256": current,
        "loaded_plugin_sha256": LOADED_SHA256,
        "restart_required": current != LOADED_SHA256,
        "python": sys.executable,
        "pid": os.getpid(),
    }
