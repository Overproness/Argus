#!/usr/bin/env python3
"""Measure static bug recall, paired clearance, controls and initial queue ranking.

This is development data. No model is called, no oracle is substituted for an
investigation, and extra unlabelled findings are not counted as false positives.
"""
import argparse
import json
import subprocess
import sys
import time
from pathlib import Path
from build import source_hash
from verify import check_integrity

BASE = Path(__file__).resolve().parent
DEFAULT_ARGUS = BASE.parents[1]


def match(finding, expected):
    return (finding["rule"] == expected["rule"] and finding["function"] == expected["function"]
            and finding["file"].replace("\\", "/") == expected["file"])


def queue_ids(items):
    ids = set()
    for item in items:
        for finding in item.get("findings", []):
            ids.add(finding["id"])
            ids.update(finding.get("same_effect", []))
    return ids


def map_target(argus, target):
    start = time.monotonic()
    completed = subprocess.run([sys.executable, str(argus / "plugins/argus/scripts/auditor_cli.py"),
                                "map", str(target)], capture_output=True, text=True, encoding="utf-8", timeout=180)
    if completed.returncode:
        raise RuntimeError((completed.stderr or completed.stdout)[-2000:])
    result = json.loads((target / ".audit/map.json").read_text(encoding="utf-8"))
    if result["meta"]["parse_errors"] or result["meta"].get("skipped_large_files"):
        raise RuntimeError(f"Incomplete map: {result['meta']}")
    return result, time.monotonic() - start


def score(bundle, argus, budget=10):
    manifest = json.loads((bundle / "judge/manifest.json").read_text(encoding="utf-8"))
    # Import the chosen Argus's deterministic ranking, not another installed copy.
    sys.path.insert(0, str(argus / "plugins/argus/scripts"))
    from auditor import orchestrate
    if not Path(orchestrate.__file__).resolve().is_relative_to(argus):
        raise ValueError("Loaded a different Argus installation; run the scorer in a fresh process")
    plugin_hash, _ = source_hash(argus / "plugins/argus")
    samples = {s["id"]: s for s in manifest["samples"]}
    maps = {}
    elapsed = {}
    for sample in manifest["samples"]:
        check_integrity(bundle, sample)
        maps[sample["id"]], elapsed[sample["id"]] = map_target(argus, bundle / sample["path"])
    cases = []
    for case in manifest["cases"]:
        buggy = maps[case["buggy_sample"]]
        fixed = maps[case["fixed_sample"]]
        before = [f for f in buggy["findings"] if match(f, case)]
        after = [f for f in fixed["findings"] if match(f, case)]
        cases.append(dict(id=case["id"], rule=case["rule"], function=case["function"],
                          buggy_hits=len(before), fixed_hits=len(after),
                          detected=bool(before), cleared=bool(before) and not after,
                          persisted_after_fix=bool(after)))
    clean = next(s for s in samples.values() if s["role"] == "clean")
    controls = [dict(**control, hits=sum(match(f, control) for f in maps[clean["id"]]["findings"]))
                for control in manifest["controls"]]
    mixed = next(s for s in samples.values() if s["role"] == "mixed")
    audit = bundle / mixed["path"] / ".audit"
    if (audit / "verdicts.json").exists():
        raise ValueError("Mixed target has an investigation ledger; build a fresh bundle for an initial-ranking comparison")
    defaults = dict(orchestrate.DEFAULT_BUDGET)
    budgets = sorted({5, 10, 15, budget})
    ranking = []
    for k in budgets:
        queue = orchestrate.queue(audit, {"total": k, "per_round": k, "max_rounds": 1}, dry_run=True)
        selected = queue_ids(queue["items"])
        covered = []
        for case in manifest["cases"]:
            hits = [f for f in maps[mixed["id"]]["findings"] if match(f, case)]
            if any(orchestrate.finding_id(f) in selected for f in hits):
                covered.append(case["id"])
        ranking.append(dict(k=k, investigations=len(queue["items"]), bugs_covered=covered,
                            recall=len(covered) / len(cases), interpretation="initial top-k only, including same-effect coverage; not multi-round investigation recall"))
    default_queue = orchestrate.queue(audit, dry_run=True)
    result = dict(schema=1, split=manifest["split"], argus_plugin_sha256=plugin_hash,
                  argus_unchanged_since_creation=plugin_hash == manifest["argus_plugin_sha256_at_creation"],
                  cases=cases, static_detected=sum(c["detected"] for c in cases),
                  paired_cleared=sum(c["cleared"] for c in cases), planted=len(cases),
                  controls=controls, control_violations=sum(bool(c["hits"]) for c in controls),
                  default_budget=defaults, default_first_round_items=len(default_queue["items"]), initial_ranking=ranking,
                  match_policy="Strict canonical rule + function + file; related leads outside that target are not credited.",
                  maps={sid: dict(stats=data["stats"], elapsed_s=elapsed[sid]) for sid, data in maps.items()},
                  note="No precision claim: unrelated findings have not been fully adjudicated. No LLM investigation was run.")
    dest = bundle / "judge/static-score.json"
    dest.write_text(json.dumps(result, indent=2), encoding="utf-8")
    lines = ["# Relaybench static development score", "",
             f"Detected: **{result['static_detected']}/{len(cases)}**. Cleared on the fix: **{result['paired_cleared']}/{len(cases)}**.",
             f"Targeted clean-control violations: **{result['control_violations']}/{len(controls)}**.",
             "", "Synthetic, authored with knowledge of Argus. These numbers are not held-out performance or reproduction success.",
             "Strict canonical rule + function + file matching. Related leads elsewhere are not credited.",
             "", "| Case | Rule | Buggy hits | Fixed hits | Pair clears |", "|---|---|---:|---:|---|"]
    for case in cases:
        lines.append(f"| {case['id']} | {case['rule']} | {case['buggy_hits']} | {case['fixed_hits']} | {case['cleared']} |")
    lines += ["", "Initial mixed-target queue ranking (no verdict feedback):", ""]
    for rank in ranking:
        lines.append(f"- Top {rank['k']}: {len(rank['bugs_covered'])}/{len(cases)} labelled bugs covered.")
    lines += ["", f"Actual frozen Argus defaults: `{defaults}`. Grouped investigations, not raw findings, consume the budget.",
              "", result["note"], "", f"Plugin hash: `{plugin_hash}`; unchanged since creation: `{result['argus_unchanged_since_creation']}`."]
    (bundle / "judge/static-score.md").write_text("\n".join(lines) + "\n", encoding="utf-8")
    return result


def main():
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--bundle", type=Path, required=True)
    ap.add_argument("--argus-root", type=Path, default=DEFAULT_ARGUS)
    ap.add_argument("--budget", type=int, default=10)
    args = ap.parse_args()
    if args.budget <= 0:
        ap.error("--budget must be positive")
    result = score(args.bundle.resolve(), args.argus_root.resolve(), args.budget)
    print(f"Static detection {result['static_detected']}/{result['planted']}; paired clearance {result['paired_cleared']}/{result['planted']}")
    print(f"Targeted controls violated: {result['control_violations']}/{len(result['controls'])}")
    print("Saved evaluator-only judge/static-score.json and .md")
    return 0  # detector misses are measurements, not invalid benchmark data


if __name__ == "__main__":
    raise SystemExit(main())
