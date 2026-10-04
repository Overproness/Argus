import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "plugins" / "argus" / "scripts"))

FIXTURES = ROOT / "tests" / "fixtures"
collect_ignore_glob = ["fixtures/*"]
# tests/eval needs PyYAML (tests/eval/requirements.txt) and clones real repos for `kind: git` cases, so it
# runs as its own job (nightly / workflow_dispatch in ci.yml), not as part of `pytest tests`. This glob
# only excludes it from directory recursion; a file given explicitly still runs:
# `pip install -r tests/eval/requirements.txt && pytest tests/eval/test_score_investigation.py`.
collect_ignore_glob += ["eval/*"]
