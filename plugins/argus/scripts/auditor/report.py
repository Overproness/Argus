"""Serialize a RepoMap to .audit/map.json and a human/agent-readable map.md."""
from __future__ import annotations

import json
from collections import Counter
from dataclasses import asdict
from datetime import datetime, timezone
from pathlib import Path

from . import paths
from .analysis import RepoMap


def to_json(m: RepoMap) -> dict:
    fns = m.functions
    langs = Counter(f.lang for f in m.files.values())
    precise_files = m.precise.files if m.precise else set()
    return {
        "meta": {
            "tool": "argus/map",
            "version": 2,
            "root": str(m.root),
            "path_warnings": paths.warnings(m.root),
            "generated_at": datetime.now(timezone.utc).isoformat(timespec="seconds"),
            "languages": dict(langs.most_common()),
            "resolution": {
                "scip_files": len(precise_files & set(m.files)),
                "name_based_files": len(set(m.files) - precise_files),
            },
            "include_tests": m.include_tests,
            "parse_errors": m.parse_errors,
            "skipped_large_files": m.skipped_large,
        },
        "stats": {
            "files": len(m.files),
            "functions": len(fns),
            "async_functions": sum(f.is_async for f in fns.values()),
            "edges": len(m.edges),
            "boundaries": len(m.boundaries),
            "findings": len(m.findings),
            "by_severity": dict(Counter(f.severity for f in m.findings)),
        },
        "findings": [asdict(f) for f in m.findings],
        "boundaries": [{
            "function": fns[b.function].qualname,
            "lang": fns[b.function].lang,
            "location": f"{fns[b.function].file}:{b.call.line}",
            "kind": b.kind,
            "category": b.category,
            "blocking": b.blocking,
            "context": b.call.context,
            "has_timeout": m.bounded(b),
            "default_timeout": b.default_timeout,
            "loop_depth": b.call.loop_depth,
            "confidence": b.confidence,
            "timeout_s": b.call.timeout_s,
            "snippet": b.call.snippet,
        } for b in m.boundaries],
        "effects": m.effects,
        "hotspots": m.hotspots(),
        "functions": [{
            "id": f.id, "lang": f.lang, "qualname": f.qualname, "is_async": f.is_async,
            "is_entry": f.is_entry, "lines": [f.line, f.end_line], "max_loop_depth": f.max_loop_depth,
        } for f in fns.values()],
        "edges": [{
            "caller": fns[e.caller].qualname, "callee": fns[e.callee].qualname,
            "caller_id": e.caller, "callee_id": e.callee,
            "line": e.line, "awaited": e.awaited, "context": e.context,
            "loop_depth": e.loop_depth, "confidence": e.confidence, "deadline_s": e.timeout_s,
        } for e in m.edges],
    }


def _cell(s) -> str:
    return str(s).replace("|", "\\|").replace("\n", " ")


def to_markdown(data: dict, max_findings: int = 200, max_rows: int = 150) -> str:
    s, meta, out = data["stats"], data["meta"], []
    out.append("# Repo audit map\n")
    out.append(f"Root: `{meta['root']!r}`\n")
    for w in meta.get("path_warnings", []):
        out.append(f"> **Path warning:** {w}\n")
    langs = ", ".join(f"{k} ({v})" for k, v in meta["languages"].items())
    out.append(
        f"{s['files']} files [{langs}] · {s['functions']} functions ({s['async_functions']} async) · "
        f"{s['edges']} call edges · {s['boundaries']} I/O boundaries · {s['findings']} findings "
        f"{s['by_severity']}\n"
    )
    res = meta["resolution"]
    out.append(f"_Call resolution: {res['scip_files']} files precise (SCIP), {res['name_based_files']} "
               "name-based. Findings are leads, not verdicts: each needs evidence before it counts._\n")
    if meta["parse_errors"]:
        out.append(f"_{len(meta['parse_errors'])} files failed to parse (see map.json)._\n")

    out.append("## Findings\n")
    if not data["findings"]:
        out.append("None.\n")
    for f in data["findings"][:max_findings]:
        out.append(f"- **[{f['severity']}] {f['rule']}**: `{f['function']}` at "
                   f"`{f['file']}:{f['line']}` ({f['lang']}, {f['confidence']})  ")
        out.append(f"  {f['message']}")
        if f["chain"]:
            out.append(f"  - chain: {' → '.join(f['chain'])}")
        if f["reached_from"]:
            out.append(f"  - reached from: {', '.join(f['reached_from'])}")
    if len(data["findings"]) > max_findings:
        out.append(f"\n_…{len(data['findings']) - max_findings} more in map.json._")
    out.append("")

    out.append("## I/O boundary inventory\n")
    out.append("| Function | Location | Kind | Blocking | Context | Timeout | Loop | Conf |")
    out.append("|---|---|---|---|---|---|---|---|")
    for b in data["boundaries"][:max_rows]:
        timeout = "yes" if b["has_timeout"] else (f"default {b['default_timeout'].split(' ')[0]}"
                                                  if b["default_timeout"] else "**no**")
        if b["category"] not in ("net",):
            timeout = "n/a" if not b["has_timeout"] else "yes"
        out.append("| " + " | ".join(_cell(x) for x in (
            f"`{b['function']}`", b["location"], f"{b['kind']} ({b['category']})",
            "yes" if b["blocking"] else "no", b["context"], timeout, b["loop_depth"], b["confidence"],
        )) + " |")
    if len(data["boundaries"]) > max_rows:
        out.append(f"\n_…{len(data['boundaries']) - max_rows} more in map.json._")
    out.append("")

    fx = data.get("effects") or {}
    if fx.get("entries") or fx.get("deadlines") or fx.get("retries"):
        out.append("## Effects across the call graph\n")
        out.append("_Static upper bounds: timeouts, library defaults and retry counts multiplied along each path._\n")
    if fx.get("entries"):
        out.append("**Entry points**: longest wait one activation can hit\n")
        out.append("| Entry | Location | Worst wait | How | Max attempts per request |")
        out.append("|---|---|---|---|---|")
        for e in fx["entries"]:
            how = e["formula"] or ""
            path = " → ".join(e["path"][-3:])
            out.append(f"| `{_cell(e['function'])}` | {e['location']} | {e['max_wait']} | "
                       f"{_cell(how)} via {_cell(path)} | {e['max_attempts_per_request'] or '∞'} |")
        out.append("")
    if fx.get("deadlines"):
        out.append("**Deadlines**: an outer timeout around an inner call\n")
        out.append("| Caller | Callee | Location | Deadline | Inner worst wait | Status |")
        out.append("|---|---|---|---|---|---|")
        for d in fx["deadlines"]:
            out.append(f"| `{_cell(d['caller'])}` | `{_cell(d['callee'])}` | {d['location']} | "
                       f"{d['deadline_s']:g} s | {_cell(d['inner_wait'])} | {d['status']} |")
        out.append("")
    if fx.get("retries"):
        out.append("**Retry sites**\n")
        out.append("| Function | Location | Kind | Attempts | Backoff |")
        out.append("|---|---|---|---|---|")
        for r in fx["retries"]:
            att = "∞" if r["attempts"] is None else (r["attempts"] or "bounded (policy/counter)")
            out.append(f"| `{_cell(r['function'])}` | {r['location']} | {r['kind']} | {att} | {r['backoff']} |")
        out.append("")

    out.append("## Hotspots\n")
    out.append("| Score | Function | Location | Loop nesting | Fan-in | Async |")
    out.append("|---|---|---|---|---|---|")
    for h in data["hotspots"]:
        out.append(f"| {h['score']} | `{_cell(h['function'])}` | {h['location']} | "
                   f"{h['loop_nesting']} | {h['fan_in']} | {'yes' if h['is_async'] else 'no'} |")
    out.append("")
    return "\n".join(out)


def write(m: RepoMap, out_dir: Path) -> tuple[Path, Path]:
    out_dir.mkdir(parents=True, exist_ok=True)
    data = to_json(m)
    jp, mp = out_dir / "map.json", out_dir / "map.md"
    jp.write_text(json.dumps(data, indent=2), encoding="utf8")
    mp.write_text(to_markdown(data), encoding="utf8")
    return jp, mp
