import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "plugins" / "argus" / "scripts"))

FIXTURES = ROOT / "tests" / "fixtures"
collect_ignore_glob = ["fixtures/*"]
