"""Starting other programs the same way on every OS."""
from __future__ import annotations

import os
import shutil
from pathlib import Path


def resolve(cmd: list) -> list[str]:
    """Look the program up the way a shell would. On Windows, npm, npx, yarn, pnpm, mvn, gradle and
    many other tools are .cmd or .bat shims that CreateProcess does not find by their bare name."""
    cmd = [str(c) for c in cmd]
    if os.name != "nt" or not cmd or os.path.dirname(cmd[0]) or Path(cmd[0]).suffix.lower() in (".exe", ".com"):
        return cmd
    exe = shutil.which(cmd[0])
    return [exe, *cmd[1:]] if exe else cmd
