#!/usr/bin/env python3
"""Small helpers for action.yml. Kept as real scripts, not inline YAML heredocs, so they can be tested
and so YAML's block-scalar indentation can never silently break Python's indentation-sensitive syntax.

  ci_helpers.py outputs <map.json>          write GITHUB_OUTPUT lines: high=N, medium=N, low=N, info=N, map_json=...
  ci_helpers.py gate <map.json> <severity>  exit 1 (and print a ::error::) if any finding is at or above it
"""
from __future__ import annotations

import json
import os
import sys
from pathlib import Path

ORDER = ["high", "medium", "low", "info"]


def outputs(map_json: Path) -> int:
    data = json.loads(map_json.read_text(encoding="utf8"))
    sev = {s: sum(f["severity"] == s for f in data["findings"]) for s in ORDER}
    out = os.environ.get("GITHUB_OUTPUT")
    lines = [f"{k}={v}" for k, v in sev.items()] + [f"map_json={map_json.resolve()}"]
    if out:
        with open(out, "a", encoding="utf8") as f:
            f.write("\n".join(lines) + "\n")
    else:
        print("\n".join(lines))
    return 0


def gate(map_json: Path, threshold: str) -> int:
    if threshold not in ORDER:
        print(f"gate: unknown severity {threshold!r}, expected one of {ORDER}", file=sys.stderr)
        return 2
    data = json.loads(map_json.read_text(encoding="utf8"))
    at_or_above = ORDER[: ORDER.index(threshold) + 1]
    hits = [f for f in data["findings"] if f["severity"] in at_or_above]
    if hits:
        print(f"::error::{len(hits)} finding(s) at {threshold} severity or above")
        return 1
    return 0


def main() -> int:
    if len(sys.argv) >= 3 and sys.argv[1] == "outputs":
        return outputs(Path(sys.argv[2]))
    if len(sys.argv) >= 4 and sys.argv[1] == "gate":
        return gate(Path(sys.argv[2]), sys.argv[3])
    print(__doc__, file=sys.stderr)
    return 2


if __name__ == "__main__":
    sys.exit(main())
