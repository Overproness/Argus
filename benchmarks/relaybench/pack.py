#!/usr/bin/env python3
"""Pack one opaque target; omit answer keys, Git history and audit artifacts."""
import argparse
import json
import zipfile
from pathlib import Path
from verify import check_integrity


def pack(bundle, sample_id, output):
    manifest = json.loads((bundle / "judge/manifest.json").read_text(encoding="utf-8"))
    sample = next((s for s in manifest["samples"] if s["id"] == sample_id), None)
    if sample is None:
        raise ValueError("Unknown sample")
    check_integrity(bundle, sample)
    if output.exists():
        raise FileExistsError(f"Refusing to replace {output}")
    output.parent.mkdir(parents=True, exist_ok=True)
    with zipfile.ZipFile(output, "w", compression=zipfile.ZIP_DEFLATED) as archive:
        for relative in sample["files"]:
            # Modification times should not disclose which file the seed changed.
            entry = zipfile.ZipInfo(relative, date_time=(2026, 1, 1, 0, 0, 0))
            entry.compress_type = zipfile.ZIP_DEFLATED
            entry.external_attr = 0o100644 << 16
            archive.writestr(entry, (bundle / sample["path"] / relative).read_bytes())
    return len(sample["files"])


def main():
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--bundle", type=Path, required=True)
    ap.add_argument("--sample", required=True)
    ap.add_argument("--out", type=Path, required=True)
    args = ap.parse_args()
    count = pack(args.bundle.resolve(), args.sample, args.out.resolve())
    print(f"Packed {count} source files into {args.out}; no answer key or Git history")


if __name__ == "__main__":
    main()
