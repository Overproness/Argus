"""Registry of language specs, keyed by file extension."""
from pathlib import Path

from . import cfamily, csharp, golang, javascript, jvm, patterns, php, python, ruby, rust, swift
from .base import LangSpec

SPECS: list[LangSpec] = [
    rust.SPEC, python.SPEC, javascript.SPEC, javascript.TS_SPEC, javascript.TSX_SPEC, golang.SPEC,
    jvm.JAVA, jvm.KOTLIN, jvm.SCALA, csharp.SPEC, swift.SPEC, cfamily.C, cfamily.CPP, ruby.SPEC, php.SPEC,
]
for _s in SPECS:
    if patterns.pattern_rules not in _s.hooks:
        _s.hooks.append(patterns.pattern_rules)
BY_EXT = {ext: s for s in SPECS for ext in s.extensions}


def spec_for(path: Path) -> LangSpec | None:
    return BY_EXT.get(path.suffix.lower())
