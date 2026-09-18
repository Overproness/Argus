#!/usr/bin/env python3
"""Argus MCP server: audit_map, audit_trace and run_repro over stdio.

Thin wrappers over the same functions the CLI calls, so any MCP client
(Claude Code, Codex, Cursor, a CI job) can drive the audit without the skills.
"""
from __future__ import annotations

import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

from mcp.server.mcpserver import MCPServer  # noqa: E402

server = MCPServer(
    "argus",
    instructions="Static audit map, runtime tracing and reproduction runner for a repository. "
    "Run audit_map first; audit_trace and run_repro read its .audit/map.json.",
)


def _out(repo: Path, out: str | None) -> Path:
    return Path(out).resolve() if out else repo / ".audit"


@server.tool()
def audit_map(repo: str, include_tests: bool = False, out: str | None = None, max_findings: int = 50) -> dict:
    """Build the static audit map: call graph, I/O boundaries, findings, hotspots. Writes .audit/map.{json,md}."""
    from auditor import report
    from auditor.analysis import RepoMap
    from auditor import scip

    root = Path(repo).resolve()
    out_dir = _out(root, out)
    precise = scip.load(out_dir / "scip")
    m = RepoMap(root, include_tests=include_tests, precise=precise).load()
    if not m.files:
        return {"error": f"no supported source files under {root}"}
    jp, mp = report.write(m, out_dir)
    data = json.loads(jp.read_text(encoding="utf8"))
    return {"map_json": str(jp), "map_md": str(mp), "stats": data["stats"],
            "findings": data["findings"][:max_findings], "hotspots": data["hotspots"][:10]}


@server.tool()
def audit_trace(repo: str, command: list[str], stall_ms: float = 100, shapes: bool = True,
                out: str | None = None) -> dict:
    """Run `command` (argv list) under the Python runtime tracer and join the trace to map.json. Writes .audit/trace.{json,md}."""
    from auditor.trace import evidence, run as trace_run, store

    root = Path(repo).resolve()
    out_dir = _out(root, out)
    map_path = out_dir / "map.json"
    if not map_path.exists():
        return {"error": f"{map_path} missing; call audit_map first"}
    rc = trace_run.run(root, out_dir / "trace", command, stall_ms, shapes)
    t = store.load(out_dir / "trace")
    if not t.calls:
        return {"error": "no trace data recorded (traced program must be Python 3.12+ inside the repo)",
                "exit_code": rc}
    jp, mp = evidence.write(map_path, t, out_dir)
    data = json.loads(jp.read_text(encoding="utf8"))
    return {"exit_code": rc, "trace_json": str(jp), "trace_md": str(mp), "summary": data["summary"],
            "findings": [{k: f[k] for k in ("rule", "severity", "function", "file", "line")} | {"evidence": f["evidence"]}
                         for f in data["findings"]],
            "unpredicted_stalls": data["unpredicted_stalls"], "complexity": data["complexity"]}


@server.tool()
def run_repro(repo: str, file: str | None = None, timeout: int = 600, out: str | None = None) -> dict:
    """Run reproduction tests under .audit/repros (or one file) and collect outcomes and evidence. Writes .audit/repro.{json,md}."""
    from auditor.repro import runner

    root = Path(repo).resolve()
    out_dir = _out(root, out)
    res = runner.run(root, out_dir, Path(file).resolve() if file else None, timeout)
    jp, mp = runner.write(res, out_dir)
    return {"repro_json": str(jp), "repro_md": str(mp), "exit_code": res["exit_code"], "tests": res["tests"],
            "output_tail": res["output_tail"] if res["exit_code"] not in (0, 1) else ""}


if __name__ == "__main__":
    server.run("stdio")
