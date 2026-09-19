#!/usr/bin/env python3
"""argus CLI.

  auditor_cli.py map   <repo> [--out DIR] [--include-tests] [--no-scip]
  auditor_cli.py index <repo> [--out DIR] [--only rust-analyzer,scip-python,...]
  auditor_cli.py trace <repo> [--out DIR] [--stall-ms 100] [--no-shapes] [--assume ARG=N]
                              [--otlp] [--heartbeat REGEX] -- <command...>
  auditor_cli.py trace-import <repo> --otlp-file spans.json [--assume ARG=N]
  auditor_cli.py trace-report <repo> [--out DIR] [--assume ARG=N]
  auditor_cli.py repro <repo> [--out DIR] [--file test_x.py] [--timeout 600]
  auditor_cli.py queue <repo> [--budget 10] [--per-round 5] [--max-rounds 3] [--rule R] [--dry-run]
  auditor_cli.py record <repo> (--file verdicts.json | -)
  auditor_cli.py report <repo> [--out DIR]
  auditor_cli.py fault-server [--latency S | --hang | --reset | --fail-first N] [--proxy HOST:PORT] [--record F]
  auditor_cli.py probe <repo> --lang L --name N [--opt key=value ...] [--build]
  auditor_cli.py langs
"""
from __future__ import annotations

import argparse
import json
import os
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
    assume_help = "project cost at this input size: ARG=N, FUNCTION=N, FUNCTION.ARG=N or *=N (repeatable)"
    tp.add_argument("--assume", action="append", default=[], metavar="NAME=N", help=assume_help)
    tp.add_argument("--otlp", action="store_true",
                    help="receive OpenTelemetry spans from the program (any language; sets OTEL_EXPORTER_OTLP_*)")
    tp.add_argument("--heartbeat", metavar="REGEX",
                    help="timestamp output lines matching REGEX; long gaps between them are stalls (any language)")
    tp.add_argument("command", nargs="*", help="command to run, after --")
    ti = sub.add_parser("trace-import", help="import OpenTelemetry spans recorded elsewhere (OTLP JSON/protobuf files)")
    ti.add_argument("repo", type=Path)
    ti.add_argument("--out", type=Path, help="output dir (default: <repo>/.audit)")
    ti.add_argument("--otlp-file", type=Path, action="append", required=True,
                    help="collector file-exporter output, an OTLP JSON document, or raw OTLP protobuf (repeatable)")
    ti.add_argument("--assume", action="append", default=[], metavar="NAME=N", help=assume_help)
    rp = sub.add_parser("trace-report", help="rebuild trace.json/trace.md from recorded traces")
    rp.add_argument("repo", type=Path)
    rp.add_argument("--out", type=Path, help="output dir (default: <repo>/.audit)")
    rp.add_argument("--assume", action="append", default=[], metavar="NAME=N", help=assume_help)
    pp = sub.add_parser("repro", help="run reproduction tests under <out>/repros and collect evidence")
    pp.add_argument("repo", type=Path)
    pp.add_argument("--out", type=Path, help="output dir (default: <repo>/.audit)")
    pp.add_argument("--file", type=Path, help="run one reproduction file instead of all")
    pp.add_argument("--timeout", type=int, default=600, help="seconds before the whole run is killed")
    qp = sub.add_parser("queue", help="next round of findings to investigate, ranked and within budget")
    qp.add_argument("repo", type=Path)
    qp.add_argument("--out", type=Path, help="output dir (default: <repo>/.audit)")
    qp.add_argument("--budget", type=int, help="total investigations across all rounds (default 10, remembered)")
    qp.add_argument("--per-round", type=int, help="investigations per round (default 5, remembered)")
    qp.add_argument("--max-rounds", type=int, help="round cap (default 3, remembered)")
    qp.add_argument("--rule", action="append", default=[], help="only these rules (repeatable)")
    qp.add_argument("--include-low", action="store_true", help="also queue low-severity findings")
    qp.add_argument("--include-info", action="store_true", help="also queue info findings")
    qp.add_argument("--retry-inconclusive", action="store_true", help="requeue inconclusive verdicts")
    qp.add_argument("--dry-run", action="store_true", help="show the round without opening it")
    vp = sub.add_parser("record", help="record investigator verdicts in .audit/verdicts.json")
    vp.add_argument("repo", type=Path)
    vp.add_argument("--out", type=Path, help="output dir (default: <repo>/.audit)")
    vp.add_argument("--file", type=Path, help="JSON file with one verdict object or a list; '-' reads stdin")
    fp = sub.add_parser("report", help="final report from map, trace, repro and verdicts: report.{json,md,html}")
    fp.add_argument("repo", type=Path)
    fp.add_argument("--out", type=Path, help="output dir (default: <repo>/.audit)")
    sub.add_parser("fault-server", add_help=False,
                   help="stand-in for a remote dependency with injected faults (any language); --help for flags")
    np_ = sub.add_parser("probe", help="scaffold a native probe side project for a language; prints how to run it")
    np_.add_argument("repo", type=Path)
    np_.add_argument("--out", type=Path, help="output dir (default: <repo>/.audit); probes go to <out>/repros/native")
    np_.add_argument("--lang", required=True)
    np_.add_argument("--name", required=True, help="probe name: letters, digits, underscores")
    np_.add_argument("--opt", action="append", default=[], metavar="KEY=VALUE",
                     help="scaffold option (crate=, module=, project=, sources=a.c,b.c, classpath=...)")
    np_.add_argument("--build", action="store_true", help="also build it now")
    sub.add_parser("langs", help="list supported languages and their SCIP indexers")
    if sys.argv[1:2] == ["fault-server"]:  # its flags are its own
        from auditor.repro import faults
        return faults.main(sys.argv[2:])
    argv = sys.argv[1:]
    passthrough: list[str] | None = None
    if argv[:1] == ["trace"] and "--" in argv:
        # Everything after `--` belongs to the traced program, flags included.
        cut = argv.index("--")
        argv, passthrough = argv[:cut], argv[cut + 1:]
    args = ap.parse_args(argv)
    if passthrough is not None:
        args.command = passthrough

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

    if args.cmd in ("trace", "trace-report", "trace-import"):
        from auditor.trace import otlp, spans, store
        from auditor.trace import run as trace_run
        trace_dir = out_dir / "trace"
        map_path = out_dir / "map.json"
        if not map_path.exists():
            print(f"no {map_path}; run `map` first to link evidence to findings", file=sys.stderr)
            return 1
        if args.cmd == "trace":
            command = args.command[1:] if args.command[:1] == ["--"] else args.command
            if not command:
                print("trace: give the command to run after `--`", file=sys.stderr)
                return 1
            if args.otlp and "OTEL_INSTRUMENTATION_METHODS_INCLUDE" not in os.environ:
                # The Java agent spans a repo's own methods only when named: name the ones findings are about.
                methods = spans.jvm_methods_include(json.loads(map_path.read_text(encoding="utf8")))
                if methods:
                    os.environ["OTEL_INSTRUMENTATION_METHODS_INCLUDE"] = methods
                    print(f"otlp: Java agent method spans for {methods.count('[')} class(es) from the map")
            rc = trace_run.run(repo, trace_dir, command, args.stall_ms, not args.no_shapes,
                               otlp=args.otlp, heartbeat=args.heartbeat)
            print(f"command exited {rc}")
        if args.cmd == "trace-import":
            import time as _time
            n = 0
            for i, f in enumerate(args.otlp_file):
                got = otlp.load_file(f)
                otlp.write_jsonl(got, trace_dir / f"import-{int(_time.time())}-{i}.spans.jsonl",
                                 {"argv": [f"imported from {f.name}"]})
                n += len(got)
            print(f"imported {n} span(s) from {len(args.otlp_file)} file(s)")
        t = spans.attach(store.load(trace_dir), trace_dir, json.loads(map_path.read_text(encoding="utf8")), repo)
        if not (t.calls or t.stalls or t.io):
            print(f"no trace data under {trace_dir}. Python programs need 3.12+; for other languages run with "
                  f"--otlp (an OpenTelemetry-instrumented program) and/or --heartbeat REGEX", file=sys.stderr)
            return 1
        print(f"{len(t.runs)} run(s), {len(t.calls)} calls, {len(t.io)} external calls, {len(t.stalls)} stalls")
        from auditor.trace import evidence
        try:
            scale = {k.strip(): float(v) for k, v in (a.split("=", 1) for a in args.assume)}
        except ValueError:
            print("--assume takes NAME=N, e.g. --assume rows=50000", file=sys.stderr)
            return 1
        jp, mp_ = evidence.write(map_path, t, out_dir, scale)
        data = json.loads(jp.read_text(encoding="utf8"))
        print("evidence: " + ", ".join(f"{v} {k}" for k, v in sorted(data["summary"].items())))
        print(f"wrote {jp}\nwrote {mp_}")
        return 0

    if args.cmd == "probe":
        from dataclasses import asdict

        from auditor.repro import native
        opts = {}
        for kv in args.opt:
            k, _, v = kv.partition("=")
            opts[k] = v.split(",") if k in ("sources", "includes", "flags", "classpath", "deps") else v
        try:
            probe = native.scaffold(args.lang, args.name, repo=repo, out_dir=out_dir / "repros" / "native", **opts)
            if args.build:
                native.build_probe(probe)
        except (ValueError, RuntimeError, FileNotFoundError) as e:
            print(f"probe: {e}", file=sys.stderr)
            return 1
        print(json.dumps({k: (str(v) if isinstance(v, Path) else v) for k, v in asdict(probe).items()}, indent=2))
        return 0

    if args.cmd in ("queue", "record", "report"):
        from auditor import orchestrate
        if not (out_dir / "map.json").exists():
            print(f"no {out_dir / 'map.json'}; run `map` first", file=sys.stderr)
            return 1
        if args.cmd == "queue":
            res = orchestrate.queue(
                out_dir, {"total": args.budget, "per_round": args.per_round, "max_rounds": args.max_rounds},
                set(args.rule) or None, args.include_low, args.include_info, args.retry_inconclusive, args.dry_run)
            b = res["budget"]
            if res["stop"]:
                print(f"stop: {res['stop']} (budget {b['issued']}/{b['total']} used)")
            else:
                print(f"round {res['round']}: {len(res['items'])} item(s), {len(res['deferred'])} deferred, "
                      f"{len(res['skipped'])} skipped, budget {b['issued'] + len(res['items'])}/{b['total']}")
                for it in res["items"]:
                    extra = f" (+{len(it['covers'])} merged)" if it.get("covers") else ""
                    print(f"  {it['score']:>5}  {it['id']}{extra}")
            if res["pending"]:
                print(f"warning: {len(res['pending'])} item(s) from earlier rounds have no verdict: "
                      + ", ".join(res["pending"][:5]), file=sys.stderr)
            if not args.dry_run:
                print(f"wrote {out_dir / 'queue.json'}")
            return 0
        if args.cmd == "record":
            if args.file is None:
                print("record: pass --file verdicts.json (or --file - for stdin)", file=sys.stderr)
                return 1
            raw = sys.stdin.read() if str(args.file) == "-" else args.file.read_text(encoding="utf8")
            try:
                data = json.loads(raw)
            except json.JSONDecodeError as e:
                print(f"record: not JSON: {e}", file=sys.stderr)
                return 1
            res = orchestrate.record(out_dir, data if isinstance(data, list) else [data])
            print(f"recorded {len(res['recorded'])}, downgraded {len(res['downgraded'])}, "
                  f"refused {len(res['rejected_input'])}")
            for fid in res["downgraded"]:
                print(f"  downgraded to inconclusive (repro did not pass): {fid}")
            for r in res["rejected_input"]:
                print(f"  refused: {r['reason']}", file=sys.stderr)
            return 0 if not res["rejected_input"] else 1
        from auditor import report_html
        data = orchestrate.final(out_dir)
        paths = report_html.write(data, out_dir)
        print(", ".join(f"{v} {k}" for k, v in sorted(data["summary"]["by_status"].items())))
        print("\n".join(f"wrote {p}" for p in paths))
        return 0

    if args.cmd == "repro":
        from auditor.repro import runner
        res = runner.run(repo, out_dir, args.file.resolve() if args.file else None, args.timeout)
        jp, mp_ = runner.write(res, out_dir)
        counts = {}
        for t in res["tests"]:
            counts[t["outcome"]] = counts.get(t["outcome"], 0) + 1
        print(", ".join(f"{v} {k}" for k, v in sorted(counts.items())) or res["output_tail"])
        print(f"wrote {jp}\nwrote {mp_}")
        return 0 if res["exit_code"] in (0, 1) else 1

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
