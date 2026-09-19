"""The PreToolUse guard: blocks live hosts, secrets and destructive commands in audit runs."""
import json
import subprocess
import sys

import pytest
from conftest import ROOT

GUARD = ROOT / "plugins" / "argus" / "scripts" / "hooks" / "guard.py"


def hook(tool, **tool_input):
    r = subprocess.run([sys.executable, GUARD], input=json.dumps({"tool_name": tool, "tool_input": tool_input}),
                       capture_output=True, text=True)
    return r.returncode, r.stderr


@pytest.mark.parametrize("cmd", [
    "python auditor_cli.py trace . -- python bot.py --exchange api.binance.com",
    "BINANCE_API_KEY=abc python auditor_cli.py trace . -- python bot.py",
    "ENV=PROD python auditor_cli.py repro .",
    "rm -rf / --no-preserve-root",
    "git push --force origin main",
])
def test_blocked_commands(cmd):
    rc, err = hook("Bash", command=cmd)
    assert rc == 2 and "Argus guard" in err


@pytest.mark.parametrize("cmd", [
    "python auditor_cli.py trace . -- python -m pytest tests",
    "python auditor_cli.py trace . -- python bot.py --exchange testnet.binance.vision",
    "curl https://api.binance.com/api/v3/time",  # not an audit run: user's business
    "ls -la",
])
def test_allowed_commands(cmd):
    assert hook("Bash", command=cmd)[0] == 0


def test_repro_file_with_remote_url_blocked():
    rc, err = hook("Write", file_path="/r/.audit/repros/test_x.py", content="urlopen('https://api.kraken.com/0/public/Time')")
    assert rc == 2 and "live exchange" in err
    rc, err = hook("Write", file_path="/r/.audit/repros/test_x.py", content="urlopen('https://example.org/x')")
    assert rc == 0
    rc, err = hook("Write", file_path="/r/.audit/repros/test_x.py", content="urlopen('https://internal.corp/x')")
    assert rc == 2 and "remote URL" in err


def test_repro_file_with_secret_blocked():
    rc, err = hook("Edit", file_path="/r/.audit/repros/test_x.py", old_string="a", new_string="API_KEY = 'sk-live'")
    assert rc == 2


def test_ordinary_write_untouched():
    assert hook("Write", file_path="/r/src/client.py", content="API_KEY = os.environ['KEY']")[0] == 0
