"""Repo path sanity: catch the paths that silently point an audit at the wrong tree.

A directory name with leading or trailing whitespace ("app " next to "app") survives the shell
when quoted, but not a copy through prose, a prompt or a hand-typed command: the whitespace is
dropped and every read lands in the sibling. These checks name the risk up front so the path
is carried verbatim (as JSON) and the code under audit is checked by content, not by name.
"""
from __future__ import annotations

import hashlib
from pathlib import Path


def warnings(repo: Path) -> list[str]:
    out: list[str] = []
    for part in repo.parts[1:]:
        if part != part.strip():
            out.append(f"path component {part!r} has leading or trailing whitespace; pass the repo path "
                       f"exactly as {str(repo)!r} (quoted), never retyped")
            break
    parent = repo.parent
    key = repo.name.strip().casefold()
    try:
        siblings = [p for p in parent.iterdir() if p.is_dir() and p.name != repo.name
                    and p.name.strip().casefold() == key]
    except OSError:
        siblings = []
    for s in siblings:
        out.append(f"sibling directory {str(s)!r} differs from the audited {str(repo)!r} only by whitespace or "
                   f"case; a path that loses them reads the wrong project")
    return out


def file_digest(path: Path) -> str | None:
    try:
        return hashlib.sha1(path.read_bytes()).hexdigest()[:12]
    except OSError:
        return None


def excerpt(path: Path, line: int, start: int | None = None, end: int | None = None, limit: int = 40) -> str:
    """Numbered source lines: the enclosing function (start..end) when known and short, else around `line`."""
    try:
        lines = path.read_text(encoding="utf8", errors="replace").splitlines()
    except OSError:
        return ""
    if start and end and end - start + 1 <= limit:
        lo, hi = start, end
    else:
        lo, hi = max(1, line - limit // 3), line + limit - limit // 3
        if start:
            lo = max(lo, start)
    hi = min(hi, len(lines))
    return "\n".join(f"{i:>5}  {lines[i - 1]}" for i in range(lo, hi + 1))
