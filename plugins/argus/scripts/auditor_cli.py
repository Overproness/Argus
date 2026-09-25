#!/usr/bin/env python3
"""argus CLI.

  auditor_cli.py map   <repo> [--out DIR] [--include-tests] [--no-scip]
  auditor_cli.py index <repo> [--out DIR] [--only rust-analyzer,scip-python,...]
  auditor_cli.py trace <repo> [--out DIR] [--stall-ms 100] [--no-shapes] [--assume ARG=N]
                              [--otlp] [--heartbeat REGEX] [--node | --no-node] -- <command...>
  auditor_cli.py trace-import <repo> [--otlp-file spans.json] [--profile x.cpuprofile|x.speedscope.json] [--chrome trace.json]
                                     [--coverage v8-coverage-dir] [--stall-ms 100] [--assume ARG=N]
  auditor_cli.py lint-import <repo> [--sarif FILE ...] [--run [ruff,golangci-lint,semgrep]]
  auditor_cli.py trace-report <repo> [--out DIR] [--stall-ms 100] [--assume ARG=N]
  auditor_cli.py repro <repo> [--out DIR] [--file test_x.py] [--timeout 600] [--python PY]
  auditor_cli.py repro-env <repo> [--out DIR] [--python BASE_PY] [--with PKG ...]
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

import bootstrap  # noqa: E402

bootstrap.ensure_dependencies()  # a plain `python3` may lack tree-sitter and friends: use (or build) a private venv


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
    stall_help = "how long the loop must be blocked to count as a stall"
    tp.add_argument("--stall-ms", type=float, default=100, help=stall_help)
    tp.add_argument("--no-shapes", action="store_true", help="do not record argument sizes")
    assume_help = "project cost at this input size: ARG=N, FUNCTION=N, FUNCTION.ARG=N or *=N (repeatable)"
    tp.add_argument("--assume", action="append", default=[], metavar="NAME=N", help=assume_help)
    tp.add_argument("--otlp", action="store_true",
                    help="receive OpenTelemetry spans from the program (any language; sets OTEL_EXPORTER_OTLP_*)")
    tp.add_argument("--heartbeat", metavar="REGEX",
                    help="timestamp output lines matching REGEX; long gaps between them are stalls (any language)")
    tp.add_argument("--node", action="store_true",
                    help="V8 sampling profile + exact call counts for Node processes (on automatically for "
                         "node/npm/npx/yarn/pnpm/tsx/ts-node commands)")
    tp.add_argument("--no-node", action="store_true", help="do not profile Node processes")
    tp.add_argument("command", nargs="*", help="command to run, after --")
    ti = sub.add_parser("trace-import", help="import runtime data recorded elsewhere: spans, profiles, coverage")
    ti.add_argument("repo", type=Path)
    ti.add_argument("--out", type=Path, help="output dir (default: <repo>/.audit)")
    ti.add_argument("--otlp-file", type=Path, action="append", default=[],
                    help="collector file-exporter output, an OTLP JSON document, or raw OTLP protobuf (repeatable)")
    ti.add_argument("--profile", type=Path, action="append", default=[],
                    help="a V8 .cpuprofile or a speedscope JSON (py-spy, ...) (repeatable)")
    ti.add_argument("--chrome", type=Path, action="append", default=[],
                    help="Chrome trace-event JSON, e.g. Rust tracing-chrome output (repeatable)")
    ti.add_argument("--coverage", type=Path, action="append", default=[],
                    help="V8 coverage JSON (a NODE_V8_COVERAGE file or directory) for exact call counts (repeatable)")
    ti.add_argument("--stall-ms", type=float, default=100, help=stall_help + " (profiles are read with it)")
    ti.add_argument("--assume", action="append", default=[], metavar="NAME=N", help=assume_help)
    lp = sub.add_parser("lint-import", help="import linter results (SARIF) as evidence for map findings")
    lp.add_argument("repo", type=Path)
    lp.add_argument("--out", type=Path, help="output dir (default: <repo>/.audit)")
    lp.add_argument("--sarif", type=Path, action="append", default=[], help="SARIF file (repeatable)")
    lp.add_argument("--run", nargs="?", const="", metavar="NAMES", help="also run installed linters (ruff, "
                    "golangci-lint, semgrep) and read their SARIF; optional comma-separated subset")
    lp.add_argument("--semgrep-config", help="semgrep rules to use with --run (path or registry pack)")
    rp = sub.add_parser("trace-report", help="rebuild trace.json/trace.md from recorded traces")
    rp.add_argument("repo", type=Path)
    rp.add_argument("--out", type=Path, help="output dir (default: <repo>/.audit)")
    rp.add_argument("--stall-ms", type=float, default=100, help=stall_help + " (profiles are read with it)")
    rp.add_argument("--assume", action="append", default=[], metavar="NAME=N", help=assume_help)
    pp = sub.add_parser("repro", help="run reproduction tests under <out>/repros and collect evidence")
    pp.add_argument("repo", type=Path)
    pp.add_argument("--out", type=Path, help="output dir (default: <repo>/.audit)")
    pp.add_argument("--file", type=Path, help="run one reproduction file instead of all")
    pp.add_argument("--timeout", type=int, default=600, help="seconds before the whole run is killed")
    pp.add_argument("--python", help="interpreter to run the tests with (default: the repo's environment, "
                                     "<out>/venv, .venv or venv, else argus's own)")
    ep = sub.add_parser("repro-env", help="create <out>/venv with the repo's dependencies and pytest, so "
                                          "reproductions import the real code")
    ep.add_argument("repo", type=Path)
    ep.add_argument("--out", type=Path, help="output dir (default: <repo>/.audit)")
    ep.add_argument("--python", help="base interpreter for the venv (default: python3 on PATH)")
    ep.add_argument("--with", dest="extra", action="append", default=[], metavar="PKG",
                    help="extra package to install (e.g. httpx for ASGI test clients)")
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
    if not repo.is_dir():
        print(f"{args.cmd}: {str(repo)!r} is not a directory", file=sys.stderr)
        return 2
    from auditor import paths
    for w in paths.warnings(repo):
        print(f"warning: {w}", file=sys.stderr)

    if args.cmd in ("trace", "trace-report", "trace-import"):
        from auditor.trace import sources, spans
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
            node = (args.node or trace_run.is_node_command(command)) and not args.no_node
            if node:
                print("node: sampling profile (--cpu-prof) and exact call counts (NODE_V8_COVERAGE) on")
            rc = trace_run.run(repo, trace_dir, command, args.stall_ms, not args.no_shapes,
                               otlp=args.otlp, heartbeat=args.heartbeat, node=node)
            print(f"command exited {rc}")
        if args.cmd == "trace-import":
            if not (args.otlp_file or args.profile or args.coverage or args.chrome):
                print("trace-import: pass --otlp-file, --profile, --chrome and/or --coverage", file=sys.stderr)
                return 1
            try:
                got = sources.import_files(trace_dir, args.otlp_file, args.profile, args.coverage, args.chrome)
            except ValueError as e:
                print(f"trace-import: {e}", file=sys.stderr)
                return 1
            print(f"imported {got['spans']} span(s), {got['profiles']} profile(s), "
                  f"{got['coverage_files']} coverage file(s), {got['chrome']} chrome trace(s)")
        map_data = json.loads(map_path.read_text(encoding="utf8"))
        t = sources.collect(trace_dir, map_data, repo, args.stall_ms / 1000)
        if not (t.calls or t.stalls or t.io or t.counts):
            print(f"no trace data under {trace_dir}. Python programs need 3.12+; Node is profiled automatically; "
                  f"other languages need --otlp (an OpenTelemetry-instrumented program), --heartbeat REGEX, or an "
                  f"imported profile (trace-import --profile)", file=sys.stderr)
            return 1
        print(f"{len(t.runs)} run(s), {len(t.calls)} calls, {len(t.io)} external calls, {len(t.stalls)} stalls, "
              f"{len(t.counts)} exact call counts")
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

    if args.cmd == "lint-import":
        from auditor import linters
        map_path = out_dir / "map.json"
        if not map_path.exists():
            print(f"no {map_path}; run `map` first", file=sys.stderr)
            return 1
        files = [p.resolve() for p in args.sarif]
        if args.run is not None:
            for name, status in linters.run_linters(repo, out_dir, [n for n in args.run.split(",") if n] or None,
                                                    args.semgrep_config):
                print(f"{name}: {status}")
            files += sorted((out_dir / "lint").glob("*.sarif"))
        if not files:
            print("lint-import: pass --sarif FILE and/or --run", file=sys.stderr)
            return 1
        try:
            jp, mp_, data = linters.write(repo, out_dir, files)
        except (ValueError, OSError, json.JSONDecodeError) as e:
            print(f"lint-import: {e}", file=sys.stderr)
            return 1
        t = data["totals"]
        print(f"{t['results']} result(s): {t['corroborated_findings']} map finding(s) corroborated, {t['leads']} other lead(s)")
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
            for w in res.get("path_warnings", []):
                print(f"warning: {w}", file=sys.stderr)
            if res["stop"]:
                print(f"stop: {res['stop']} (budget {b['issued']}/{b['total']} used)")
            else:
                print(f"round {res['round']}: {len(res['items'])} item(s), {len(res['deferred'])} deferred, "
                      f"{len(res['skipped'])} skipped, budget {b['issued'] + len(res['items'])}/{b['total']}")
                for it in res["items"]:
                    more = [f["id"] for f in it["findings"][1:]]
                    print(f"  {it['score']:>5}  {it['id']}" + (f"  + {', '.join(more)}" if more else ""))
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
                  f"refused {len(res['rejected_input'])}"
                  + (f", applied to {len(res['applied_to_same_effect'])} same-effect finding(s)"
                     if res["applied_to_same_effect"] else ""))
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
        try:
            res = runner.run(repo, out_dir, args.file.resolve() if args.file else None, args.timeout, args.python)
        except (ValueError, FileNotFoundError) as e:
            print(f"repro: {e}", file=sys.stderr)
            return 2
        counts = {}
        for t in res["tests"]:
            counts[t["outcome"]] = counts.get(t["outcome"], 0) + 1
        print(", ".join(f"{v} {k}" for k, v in sorted(counts.items())) or res["output_tail"])
        print(f"python: {res['meta']['python']} ({res['meta']['python_why']})")
        if args.file:
            mine = [t for t in res["tests"] if t["file"] == args.file.name]
            for t in mine:
                print(f"  {t['file']}::{t['test']}: {t['outcome']}" + (f" - {t['message'][:300]}" if t["message"] else ""))
        print(f"wrote {out_dir / 'repro.json'}\nwrote {out_dir / 'repro.md'}")
        return 0 if res["exit_code"] in (0, 1) else 1

    if args.cmd == "repro-env":
        from auditor.repro import runner
        r = runner.setup_env(repo, out_dir, args.python, args.extra)
        print("\n".join(r["log"]))
        print(f"{'ready' if r['ok'] else 'incomplete'}: {r['python']}")
        return 0 if r["ok"] else 1

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
