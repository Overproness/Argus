#!/usr/bin/env python3
"""Score a real investigation run: recall, false positives, and tokens per correct finding.

`tests/eval/run.py` scores the static map against a ground-truth corpus for free, on every push, because
it costs nothing but CPU. This script scores the next step -- queue -> investigate -> record -- which
costs real subagent tokens, so it is never run automatically; run it by hand after an `audit` or
`audit-investigate` pass you want to compare against another run (a plain-prompt audit, a previous
version of the rules, a different budget).

    python tests/eval/run.py --case orderdesk-seeded-service                 # recall before investigation
    python plugins/argus/scripts/auditor_cli.py repro-env "<repo>"
    # ... run the `audit` skill against "<repo>" ...
    python tests/eval/score_investigation.py "<repo>/.audit" --tokens-total 635376 --investigators 15
    python tests/eval/score_investigation.py "<repo>/.audit" --tokens-file costs.json  # per-investigator detail

`costs.json` (optional, instead of --tokens-total/--investigators): {"<finding-or-item-id>": tokens, ...}
one entry per investigator run, however many findings it covered -- copy the per-agent totals a session
reports as it finishes each investigator.

Reads `.audit/verdicts.json` (and `.audit/queue.json` for the budget actually spent). With `--corpus` and
`--case`, also reports recall against that case's ground truth, not just "what was queued got settled" --
so a run that skipped real bugs by never queueing them is still visible as a miss.
"""
from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]


def _load(path: Path) -> dict | None:
    return json.loads(path.read_text(encoding="utf8")) if path.exists() else None


def score(audit_dir: Path, tokens_total: float | None, investigators: int | None,
         tokens_file: Path | None, corpus: Path | None, case_id: str | None) -> dict:
    led = _load(audit_dir / "verdicts.json")
    if led is None:
        raise FileNotFoundError(f"{audit_dir / 'verdicts.json'} missing; run `queue`/`record` first")
    verdicts = led.get("verdicts", {})
    by_verdict: dict[str, list[str]] = {"confirmed": [], "rejected": [], "inconclusive": []}
    for fid, v in verdicts.items():
        by_verdict.setdefault(v.get("verdict", "?"), []).append(fid)

    per_item_tokens: dict[str, float] = {}
    if tokens_file:
        per_item_tokens = json.loads(tokens_file.read_text(encoding="utf8"))
        tokens_total = sum(per_item_tokens.values())
        investigators = len(per_item_tokens)

    rounds = led.get("rounds", [])
    n_investigations = investigators if investigators is not None else sum(len(r["items"]) for r in rounds)
    n_proven = len(by_verdict["confirmed"])

    out = {
        "audit_dir": str(audit_dir),
        "budget": led.get("budget"),
        "by_verdict": {k: len(v) for k, v in by_verdict.items()},
        "investigations": n_investigations,
        "proven": n_proven,
        "tokens_total": tokens_total,
        "tokens_per_investigation": (tokens_total / n_investigations) if tokens_total and n_investigations else None,
        "tokens_per_proven_finding": (tokens_total / n_proven) if tokens_total and n_proven else None,
    }

    if corpus and case_id:
        try:
            import yaml
        except ImportError:
            print("score: --corpus needs PyYAML: pip install -r tests/eval/requirements.txt", file=sys.stderr)
            return out
        cases = yaml.safe_load(corpus.read_text(encoding="utf8"))["cases"]
        case = next((c for c in cases if c["id"] == case_id), None)
        if case is None:
            print(f"score: no such case {case_id!r} in {corpus}", file=sys.stderr)
            return out
        planted = {(w["rule"], w["function"]) for w in case.get("expected", [])}
        proven_pairs = set()
        for fid in by_verdict["confirmed"]:
            rule, rest = fid.split("@", 1)
            proven_pairs.add((rule.split(":")[-1], rest.rsplit(":", 1)[0]))
        out["corpus_case"] = case_id
        out["corpus_planted"] = len(planted)
        out["corpus_proven"] = len(planted & proven_pairs)
        out["corpus_recall_after_investigation"] = (len(planted & proven_pairs) / len(planted)) if planted else None
        out["corpus_missed"] = sorted(f"{r}@{f}" for r, f in planted - proven_pairs)

    return out


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("audit_dir", type=Path, help="the repo's .audit directory")
    ap.add_argument("--tokens-total", type=float, help="total tokens spent across every investigator")
    ap.add_argument("--investigators", type=int, help="how many investigator runs that total covers")
    ap.add_argument("--tokens-file", type=Path, help='JSON {"id": tokens, ...}, one entry per investigator run')
    ap.add_argument("--corpus", type=Path, default=Path(__file__).resolve().parent / "corpus.yaml")
    ap.add_argument("--case", help="score recall against this corpus.yaml case id too")
    ap.add_argument("--json", type=Path, help="also write the summary here")
    args = ap.parse_args()

    if args.tokens_file and args.tokens_total:
        print("score: pass --tokens-total/--investigators or --tokens-file, not both", file=sys.stderr)
        return 2

    try:
        result = score(args.audit_dir, args.tokens_total, args.investigators, args.tokens_file,
                       args.corpus, args.case)
    except FileNotFoundError as e:
        print(f"score: {e}", file=sys.stderr)
        return 2

    print(f"investigations: {result['investigations']}  budget: {result['budget']}")
    print(f"by verdict: {result['by_verdict']}")
    if result["tokens_total"]:
        print(f"tokens: {result['tokens_total']:.0f} total, "
             f"{result['tokens_per_investigation']:.0f}/investigation, "
             f"{result['tokens_per_proven_finding']:.0f}/proven finding"
             if result["tokens_per_proven_finding"] else f"tokens: {result['tokens_total']:.0f} total "
             "(no proven findings to divide by)")
    if "corpus_case" in result:
        print(f"\ncorpus {result['corpus_case']}: {result['corpus_proven']}/{result['corpus_planted']} "
             f"planted bugs proven ({result['corpus_recall_after_investigation']:.0%})")
        for m in result["corpus_missed"]:
            print(f"  not proven: {m}")

    if args.json:
        args.json.write_text(json.dumps(result, indent=2), encoding="utf8")
        print(f"\nwrote {args.json}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
