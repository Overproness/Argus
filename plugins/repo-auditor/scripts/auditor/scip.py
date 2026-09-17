"""Precise call resolution from SCIP indexes (https://github.com/sourcegraph/scip).

SCIP is a language-neutral protobuf format that many indexers produce. We only
need occurrences (symbol + range + role), so the wire format is decoded by hand
with no protobuf dependency.
"""
from __future__ import annotations

import fnmatch
import json
import os
import re
import shutil
import subprocess
from collections import defaultdict
from dataclasses import dataclass
from pathlib import Path

from .model import Function

DEFINITION_ROLE = 0x1
_METHOD_DESCRIPTOR = re.compile(r"`?([^`/#.()\[\]\s]+)`?\([^)]*\)\.$")
SKIP_DIRS = {"node_modules", "target", ".git", ".audit", "vendor", "dist", "build", ".venv", "venv"}


# Commands for each indexer. {out} is the absolute output path; the command runs in the project root.
# rust-analyzer is verified; the rest follow each project's README and may need flag adjustments.
@dataclass(frozen=True)
class Indexer:
    cmd: tuple[str, ...]
    markers: tuple[str, ...]
    install: str
    verified: bool = False


INDEXERS = {
    "rust-analyzer": Indexer(("rust-analyzer", "scip", ".", "--output", "{out}"), ("Cargo.toml",),
                             "rustup component add rust-analyzer", verified=True),
    "scip-python": Indexer(("scip-python", "index", ".", "--project-name", "{name}", "--output", "{out}"),
                           ("pyproject.toml", "setup.py", "setup.cfg", "requirements.txt"),
                           "npm install -g @sourcegraph/scip-python"),
    "scip-typescript": Indexer(("scip-typescript", "index", "--infer-tsconfig", "--output", "{out}"),
                               ("tsconfig.json", "package.json"),
                               "npm install -g @sourcegraph/scip-typescript"),
    "scip-go": Indexer(("scip-go", "--output", "{out}"), ("go.mod",),
                       "go install github.com/sourcegraph/scip-go/cmd/scip-go@latest"),
    "scip-java": Indexer(("scip-java", "index", "--output", "{out}"),
                         ("pom.xml", "build.gradle", "build.gradle.kts", "build.sbt"),
                         "see https://sourcegraph.github.io/scip-java/ (coursier bootstrap)"),
    "scip-dotnet": Indexer(("scip-dotnet", "index", "--output", "{out}"), ("*.sln", "*.csproj"),
                           "dotnet tool install --global scip-dotnet"),
    "scip-clang": Indexer(("scip-clang", "--compdb-path=compile_commands.json", "--index-output-path={out}"),
                          ("compile_commands.json",),
                          "download from https://github.com/sourcegraph/scip-clang/releases (needs compile_commands.json)"),
    "scip-ruby": Indexer(("scip-ruby", "--index-file", "{out}", "."), ("Gemfile",),
                         "download from https://github.com/sourcegraph/scip-ruby/releases"),
    "scip-php": Indexer(("scip-php",), ("composer.json",), "composer require --dev davidrjenni/scip-php"),
}


# --- protobuf wire format ------------------------------------------------------

def _varint(b: memoryview, i: int) -> tuple[int, int]:
    shift = result = 0
    while True:
        x = b[i]
        i += 1
        result |= (x & 0x7F) << shift
        if not x & 0x80:
            return result, i
        shift += 7


def _fields(b: memoryview):
    i, n = 0, len(b)
    while i < n:
        key, i = _varint(b, i)
        fno, wt = key >> 3, key & 7
        if wt == 0:
            v, i = _varint(b, i)
        elif wt == 2:
            ln, i = _varint(b, i)
            v = b[i:i + ln]
            i += ln
        elif wt == 5:
            v, i = b[i:i + 4], i + 4
        elif wt == 1:
            v, i = b[i:i + 8], i + 8
        else:
            raise ValueError(f"unsupported wire type {wt}")
        yield fno, wt, v


def _packed(v: memoryview) -> list[int]:
    out, i = [], 0
    while i < len(v):
        x, i = _varint(v, i)
        out.append(x)
    return out


def read_occurrences(path: Path):
    """Yield (relative_path, line0, symbol, roles) for every occurrence in an index."""
    data = memoryview(path.read_bytes())
    for fno, _, doc in _fields(data):
        if fno != 2:  # Index.documents
            continue
        rel, occs = "", []
        for dfno, _, dv in _fields(doc):
            if dfno == 1:
                rel = bytes(dv).decode()
            elif dfno == 2:
                occs.append(dv)
        for occ in occs:
            rng, symbol, roles = [], "", 0
            for ofno, owt, ov in _fields(occ):
                if ofno == 1:
                    rng += _packed(ov) if owt == 2 else [ov]
                elif ofno == 2:
                    symbol = bytes(ov).decode()
                elif ofno == 3:
                    roles = ov
            if rng and symbol and not symbol.startswith("local "):
                yield rel, rng[0], symbol, roles


# --- binding to extracted functions -------------------------------------------

class Precise:
    def __init__(self):
        self.files: set[str] = set()
        self._defs: list[tuple[str, int, str, str]] = []  # (file, line1, symbol, name)
        self._refs: list[tuple[str, int, str, str]] = []
        self.targets_by_site: dict[tuple[str, int, str], list[str]] = {}

    def add_index(self, path: Path, prefix: str = ""):
        for rel, line0, symbol, roles in read_occurrences(path):
            m = _METHOD_DESCRIPTOR.search(symbol)
            rel = rel.replace("\\", "/")
            while rel.startswith("./"):
                rel = rel[2:]
            f = f"{prefix}{rel}"
            self.files.add(f)
            if not m:
                continue
            rec = (f, line0 + 1, symbol, m.group(1))
            (self._defs if roles & DEFINITION_ROLE else self._refs).append(rec)

    def bind(self, functions: dict[str, Function]):
        by_file_name = defaultdict(list)
        for fn in functions.values():
            by_file_name[(fn.file, fn.name)].append(fn)
        sym2fid: dict[str, str] = {}
        for f, line, symbol, name in self._defs:
            cands = [fn for fn in by_file_name.get((f, name), []) if fn.line <= line <= fn.end_line]
            if cands:
                sym2fid[symbol] = min(cands, key=lambda fn: line - fn.line).id
        sites = defaultdict(set)
        for f, line, symbol, name in self._refs:
            fid = sym2fid.get(symbol)
            if fid:
                sites[(f, line, name)].add(fid)
        self.targets_by_site = {k: sorted(v) for k, v in sites.items()}

    def targets(self, file: str, line: int, name: str) -> list[str]:
        return self.targets_by_site.get((file, line, name), [])


def load(scip_dir: Path) -> Precise | None:
    manifest = scip_dir / "manifest.json"
    if not manifest.exists():
        return None
    p = Precise()
    for entry in json.loads(manifest.read_text(encoding="utf8")):
        f = scip_dir / entry["file"]
        if f.exists():
            p.add_index(f, entry.get("prefix", ""))
    return p


# --- running indexers ----------------------------------------------------------

def _project_roots(repo: Path, markers: tuple[str, ...]) -> list[Path]:
    """Topmost directories containing a marker file."""
    found = []
    for dirpath, dirnames, filenames in os.walk(repo):
        if any(fnmatch.fnmatch(f, m) for m in markers for f in filenames):
            found.append(Path(dirpath))
            dirnames[:] = []  # nested projects belong to this one
            continue
        dirnames[:] = sorted(d for d in dirnames if d not in SKIP_DIRS and not d.startswith("."))
    return found


def run_indexers(repo: Path, indexers: set[str], out_dir: Path, timeout: int = 1800) -> list[str]:
    """Run each indexer on every top-level project root. Returns log lines."""
    out_dir.mkdir(parents=True, exist_ok=True)
    manifest, log = [], []
    for key in sorted(indexers):
        ix = INDEXERS[key]
        exe = shutil.which(ix.cmd[0])
        if exe is None:
            log.append(f"skip {key}: not installed ({ix.install})")
            continue
        for root in _project_roots(repo, ix.markers):
            rel = root.relative_to(repo).as_posix()
            rel = "" if rel == "." else rel
            name = f"{key}-{rel.replace('/', '_') or 'root'}.scip"
            out = (out_dir / name).resolve()
            cmd = [exe, *(a.format(out=out, name=root.name) for a in ix.cmd[1:])]
            try:
                r = subprocess.run(cmd, cwd=root, capture_output=True, text=True, timeout=timeout)
            except subprocess.TimeoutExpired:
                log.append(f"fail {key} in {rel or '.'}: timed out after {timeout}s")
                continue
            if key == "scip-php" and (root / "index.scip").exists():
                shutil.move(root / "index.scip", out)
            if r.returncode != 0 or not out.exists():
                tail = (r.stderr or r.stdout).strip().splitlines()[-3:]
                log.append(f"fail {key} in {rel or '.'}: exit {r.returncode} {' | '.join(tail)}")
                continue
            manifest.append({"file": name, "prefix": f"{rel}/" if rel else "", "indexer": key})
            note = "" if ix.verified else " (unverified indexer flags)"
            log.append(f"ok   {key} in {rel or '.'} -> {out.name}{note}")
    (out_dir / "manifest.json").write_text(json.dumps(manifest, indent=2), encoding="utf8")
    return log
