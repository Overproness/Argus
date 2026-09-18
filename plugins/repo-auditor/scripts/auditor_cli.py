#!/usr/bin/env python3
"""repo-auditor CLI.

  auditor_cli.py map   <repo> [--out DIR] [--include-tests] [--no-scip]
  auditor_cli.py index <repo> [--out DIR] [--only rust-analyzer,scip-python,...]
  auditor_cli.py trace <repo> [--out DIR] [--stall-ms 100] [--no-shapes] -- <command...>
  auditor_cli.py trace-report <repo> [--out DIR]
  auditor_cli.py langs
"""
from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
REQS = Path(__file__).resolve().parent.parent / "requirements.txt"


def main() -> int:
    ap = argparse.ArgumentParser(prog="auditor")
    sub = ap.add_subparsers(dest="cmd", required=True)
    mp = sub.add_parser("map", help="static map: call graph, I/O boundaries, findings")
    mp.add_argument("repo", type=Path)
    mp.add_argument("--out", type=Path, help="output dir (default: <repo>/.audit)")
    mp.add_argument("--include-tests", action="store_true", help="also analyze test code")
    mp.add_argument("--no-scip", action="store_true", help="ignore SCIP indexes even if present")
    ip = sub.add_parser("index", help="run SCIP indexers for precise call resolution")
    ip.add_argument("repo", type=Path)
    ip.add_argument("--out", type=Path, help="output dir (default: <repo>/.audit)")
    ip.add_argument("--only", help="comma-separated indexer names")
    tp = sub.add_parser("trace", help="run a command under the runtime tracer, then report")
    tp.add_argument("repo", type=Path)
    tp.add_argument("--out", type=Path, help="output dir (default: <repo>/.audit)")
    tp.add_argument("--stall-ms", type=float, default=100, help="self-slice length that counts as a stall")
    tp.add_argument("--no-shapes", action="store_true", help="do not record argument sizes")
    tp.add_argument("command", nargs=argparse.REMAINDER, help="command to run, after --")
    rp = sub.add_parser("trace-report", help="rebuild trace.json/trace.md from recorded traces")
    rp.add_argument("repo", type=Path)
    rp.add_argument("--out", type=Path, help="output dir (default: <repo>/.audit)")
    sub.add_parser("langs", help="list supported languages and their SCIP indexers")
    args = ap.parse_args()

    try:
        from auditor import report, scip
        from auditor.analysis import RepoMap, iter_source_files
        from auditor.langs import SPECS, spec_for
    except ImportError as e:
        print(f"missing dependency: {e}\n  pip install -r {REQS}", file=sys.stderr)
        return 2

    if args.cmd == "langs":
        for s in SPECS:
            ix = scip.INDEXERS.get(s.scip_indexer) if s.scip_indexer else None
            print(f"{s.name:<11} {' '.join(s.extensions):<32} "
                  f"async={'yes' if s.has_async else 'no ':<4} scip={s.scip_indexer or '-'}"
                  f"{'' if not ix or ix.verified else ' (unverified)'}")
        return 0

    repo = args.repo.resolve()
    out_dir = (args.out or repo / ".audit").resolve()

    if args.cmd in ("trace", "trace-report"):
        from auditor.trace import run as trace_run
        from auditor.trace import store
        trace_dir = out_dir / "trace"
        if args.cmd == "trace":
            command = args.command[1:] if args.command[:1] == ["--"] else args.command
            if not command:
                print("trace: give the command to run after `--`", file=sys.stderr)
                return 1
            rc = trace_run.run(repo, trace_dir, command, args.stall_ms, not args.no_shapes)
            print(f"command exited {rc}")
        t = store.load(trace_dir)
        if not t.calls:
            print(f"no trace data under {trace_dir} (is the command Python 3.12+ and inside {repo}?)",
                  file=sys.stderr)
            return 1
        print(f"{len(t.runs)} traced process(es), {len(t.calls)} calls, {len(t.stalls)} stalls")
        map_path = out_dir / "map.json"
        if not map_path.exists():
            print(f"no {map_path}; run `map` first to link evidence to findings", file=sys.stderr)
            return 1
        from auditor.trace import evidence
        jp, mp_ = evidence.write(map_path, t, out_dir)
        data = json.loads(jp.read_text(encoding="utf8"))
        print("evidence: " + ", ".join(f"{v} {k}" for k, v in sorted(data["summary"].items())))
        print(f"wrote {jp}\nwrote {mp_}")
        return 0

    if args.cmd == "index":
        wanted = set(args.only.split(",")) if args.only else {
            spec_for(p).scip_indexer for p in iter_source_files(repo, False)
        } - {None}
        unknown = wanted - set(scip.INDEXERS)
        if unknown:
            print(f"unknown indexers: {', '.join(sorted(unknown))}", file=sys.stderr)
            return 1
        for line in scip.run_indexers(repo, wanted, out_dir / "scip"):
            print(line)
        return 0

    precise = None if args.no_scip else scip.load(out_dir / "scip")
    m = RepoMap(repo, include_tests=args.include_tests, precise=precise).load()
    if not m.files:
        print(f"no supported source files under {repo} (see `auditor_cli.py langs`)", file=sys.stderr)
        return 1
    jp, mp_ = report.write(m, out_dir)
    sev = {s: sum(f.severity == s for f in m.findings) for s in ("high", "medium", "low", "info")}
    langs = sorted({f.lang for f in m.files.values()})
    print(f"{len(m.files)} files ({', '.join(langs)}), {len(m.functions)} functions, {len(m.edges)} edges, "
          f"{len(m.boundaries)} boundaries, findings {sev}"
          f"{', SCIP on' if precise else ''}")
    print(f"wrote {jp}\nwrote {mp_}")
    for err in m.parse_errors[:20]:
        print(f"parse error: {err}", file=sys.stderr)
    return 0


if __name__ == "__main__":
    sys.exit(main())
