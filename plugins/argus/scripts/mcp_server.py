#!/usr/bin/env python3
"""Argus MCP server: map, trace, reproduce and orchestrate an audit over stdio.

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


def _evidence(root: Path, out_dir: Path, stall_ms: float, assume: dict | None, extra: dict) -> dict:
    """Join everything under .audit/trace/ to map.json and summarize it for the caller."""
    from auditor.trace import evidence, sources

    map_path = out_dir / "map.json"
    t = sources.collect(out_dir / "trace", json.loads(map_path.read_text(encoding="utf8")), root, stall_ms / 1000)
    if not (t.calls or t.stalls or t.io or t.counts):
        return {**extra, "error": "no trace data recorded: Python programs need 3.12+ and Node is profiled "
                                  "automatically; other languages need otlp=True with an OpenTelemetry-instrumented "
                                  "program, a heartbeat regex, or an imported profile (audit_trace_import)"}
    jp, mp = evidence.write(map_path, t, out_dir, assume)
    data = json.loads(jp.read_text(encoding="utf8"))
    return {**extra, "trace_json": str(jp), "trace_md": str(mp), "summary": data["summary"],
            "findings": [{k: f[k] for k in ("rule", "severity", "function", "file", "line")} | {"evidence": f["evidence"]}
                         for f in data["findings"]],
            "unpredicted_stalls": data["unpredicted_stalls"], "complexity": data["complexity"],
            "projections": data["projections"], "external_calls": data["external_calls"]}


@server.tool()
def audit_trace(repo: str, command: list[str], stall_ms: float = 100, shapes: bool = True,
                out: str | None = None, assume: dict[str, float] | None = None, otlp: bool = False,
                heartbeat: str | None = None, node: bool | None = None) -> dict:
    """Run `command` (argv list) under runtime observation and join the evidence to map.json. Writes .audit/trace.{json,md}.

    Python 3.12+ programs are traced function by function automatically, and so are Node programs (a V8
    sampling profile plus exact call counts; `node` defaults to on for node/npm/npx/yarn/pnpm/tsx commands).
    Any language: `otlp=True` receives OpenTelemetry spans (the program must be instrumented: Java/.NET agent,
    Node/Python/Go/... SDK), and `heartbeat` (a regex for the program's periodic output lines) turns gaps
    between them into stalls. Runs accumulate in .audit/trace/; delete it to start over.
    `assume` projects super-linear functions to input sizes you expect in production, e.g. {"rows": 50000}.
    """
    from auditor.trace import run as trace_run

    root = Path(repo).resolve()
    out_dir = _out(root, out)
    if not (out_dir / "map.json").exists():
        return {"error": f"{out_dir / 'map.json'} missing; call audit_map first"}
    use_node = trace_run.is_node_command(command) if node is None else node
    rc = trace_run.run(root, out_dir / "trace", command, stall_ms, shapes, otlp=otlp, heartbeat=heartbeat,
                       node=use_node)
    return _evidence(root, out_dir, stall_ms, assume, {"exit_code": rc})


@server.tool()
def audit_trace_import(repo: str, otlp_files: list[str] | None = None, profiles: list[str] | None = None,
                       coverage: list[str] | None = None, chrome_traces: list[str] | None = None,
                       stall_ms: float = 100, out: str | None = None, assume: dict[str, float] | None = None) -> dict:
    """Import runtime data recorded elsewhere, then rebuild .audit/trace.{json,md} like audit_trace.

    otlp_files: collector file-exporter output, OTLP JSON or protobuf. profiles: a V8 .cpuprofile (Node, Deno,
    Chrome DevTools) or speedscope JSON (py-spy, rbspy, dotnet-trace, ...). coverage: V8 coverage JSON files
    or directories (NODE_V8_COVERAGE) for exact call counts. chrome_traces: Chrome trace-event JSON, e.g.
    Rust tracing-chrome output (one slice per poll of an instrumented span).
    """
    from auditor.trace import sources

    root = Path(repo).resolve()
    out_dir = _out(root, out)
    if not (out_dir / "map.json").exists():
        return {"error": f"{out_dir / 'map.json'} missing; call audit_map first"}
    try:
        got = sources.import_files(out_dir / "trace", otlp_files or [], profiles or [], coverage or [], chrome_traces or [])
    except (ValueError, OSError) as e:
        return {"error": str(e)}
    return _evidence(root, out_dir, stall_ms, assume, {"imported": got})


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


@server.tool()
def audit_queue(repo: str, budget: int | None = None, per_round: int | None = None, max_rounds: int | None = None,
                rules: list[str] | None = None, include_low: bool = False, dry_run: bool = False,
                out: str | None = None) -> dict:
    """Open the next investigation round: ranked, budgeted findings to reproduce (.audit/queue.json).

    Returns `stop` (why no round was opened) or `items` to hand to investigators, plus skipped findings
    with reasons. Budget flags are remembered in .audit/verdicts.json after the first call.
    """
    from auditor import orchestrate

    out_dir = _out(Path(repo).resolve(), out)
    if not (out_dir / "map.json").exists():
        return {"error": f"{out_dir / 'map.json'} missing; call audit_map first"}
    return orchestrate.queue(out_dir, {"total": budget, "per_round": per_round, "max_rounds": max_rounds},
                             set(rules) if rules else None, include_low, False, False, dry_run)


@server.tool()
def audit_record(repo: str, verdicts: list[dict], out: str | None = None) -> dict:
    """Record investigator verdicts ({finding, verdict: confirmed|rejected|inconclusive, ...}) in the ledger.

    A confirmed verdict whose reproduction does not pass in the latest run_repro is downgraded to inconclusive.
    """
    from auditor import orchestrate

    out_dir = _out(Path(repo).resolve(), out)
    if not (out_dir / "map.json").exists():
        return {"error": f"{out_dir / 'map.json'} missing; call audit_map first"}
    return orchestrate.record(out_dir, verdicts)


@server.tool()
def audit_report(repo: str, out: str | None = None) -> dict:
    """Write the final report from map, trace, reproductions and verdicts: .audit/report.{json,md,html}."""
    from auditor import orchestrate, report_html

    out_dir = _out(Path(repo).resolve(), out)
    if not (out_dir / "map.json").exists():
        return {"error": f"{out_dir / 'map.json'} missing; call audit_map first"}
    data = orchestrate.final(out_dir)
    paths = report_html.write(data, out_dir)
    proven = [{k: f.get(k) for k in ("id", "severity", "file", "line")} | {
        "extreme_case": (f.get("verdict") or {}).get("extreme_case"),
        "smallest_fix": (f.get("verdict") or {}).get("smallest_fix")} for f in data["findings"] if f["status"] == "proven"]
    return {"report_html": str(paths[2]), "report_md": str(paths[1]), "report_json": str(paths[0]),
            "by_status": data["summary"]["by_status"], "proven": proven, "effects": data["effects"]}


if __name__ == "__main__":
    server.run("stdio")
