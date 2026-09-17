#!/usr/bin/env python3
"""repo-auditor CLI.

  auditor_cli.py map   <repo> [--out DIR] [--include-tests] [--no-scip]
  auditor_cli.py index <repo> [--out DIR] [--only rust-analyzer,scip-python,...]
  auditor_cli.py langs
"""
from __future__ import annotations

import argparse
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
