"""Bounded batches share an investigator's setup without sharing verdicts or budget slots."""
from __future__ import annotations

import json
from pathlib import Path


PLUGIN_ROOT = Path(__file__).resolve().parents[2]
MAX_FUNCTIONS = 6
MAX_FINDINGS = 24
MAX_SOURCE_CHARS = 12000
REFERENCE = PLUGIN_ROOT / "skills/audit-investigate/references/reproduction-guide.md"


def guide_for(items: list[dict]) -> str:
    """Only this language's setup and these rules' helper rows, not the whole manual."""
    reference = REFERENCE.read_text(encoding="utf8")
    python, native = reference.split("### 3b.", 1)
    section = python if items[0]["lang"] == "python" else "### 3b." + native
    prefix = section.split("| Rule |", 1)[0]
    rules = {f["rule"].split(":")[-1] for it in items for f in it["findings"]}
    rows = [line for line in section.splitlines() if line.startswith("| ")
            and any(rule in line.split("|")[1] for rule in rules)]
    return (prefix + "| Rule | Helper / fault | Proof |\n|---|---|---|\n"
            + "\n".join(rows) + "\n\n"
            + "If a helper's signature is unclear, read only that helper in scripts/auditor/repro/. "
              "Do not read the full map, trace or reference by default.\n")


def prepare(items: list[dict], out_dir: Path, round_no: int, dry_run: bool = False) -> list[dict]:
    groups: list[list[dict]] = []
    for item in items:
        key = (item["repo"], item["lang"], item.get("abs_file") or item["id"])
        for group in groups:
            first = group[0]
            same = key == (first["repo"], first["lang"], first.get("abs_file") or first["id"])
            if (same and len(group) < MAX_FUNCTIONS
                    and sum(len(it["findings"]) for it in group) + len(item["findings"]) <= MAX_FINDINGS
                    and sum(len(it.get("source", "")) for it in group)
                    + len(item.get("source", "")) <= MAX_SOURCE_CHARS):
                group.append(item)
                break
        else:
            groups.append([item])
    batches = []
    for n, group in enumerate(groups, 1):
        folder = out_dir.resolve() / "investigations" / f"round-{round_no}"
        context_file, guide_file = folder / f"batch-{n}.json", folder / f"batch-{n}.md"
        batch = {"context_file": str(context_file), "guide_file": str(guide_file),
                 "item_ids": [it["id"] for it in group], "functions": len(group),
                 "findings": sum(len(it["findings"]) for it in group)}
        if not dry_run:
            folder.mkdir(parents=True, exist_ok=True)
            context_file.write_text(json.dumps({"plugin_root": str(PLUGIN_ROOT), "items": group}, indent=2),
                                    encoding="utf8")
            guide_file.write_text(guide_for(group), encoding="utf8")
        batches.append(batch)
    return batches
