#!/usr/bin/env python3
"""PreToolUse guard: keep audit runs away from live systems and destructive commands.

Reads the hook payload on stdin. Exit 2 blocks the tool call; the message on
stderr is shown to the agent. Exit 0 lets it through.

Scope is deliberately narrow: it only looks at (a) commands that run the
auditor's `trace` or `repro`, and (b) files written under `.audit/repros/`.
Everything else is the user's business.
"""
from __future__ import annotations

import json
import re
import sys

LIVE_HOSTS = re.compile(
    r"(?i)\b(api|stream|ws|fapi|dapi)?\.?(binance|coinbase|kraken|bybit|okx|bitfinex|bitstamp|gemini|"
    r"kucoin|gate\.io|huobi|deribit|interactivebrokers|ibkr|alpaca\.markets|polygon\.io|tradier)\.(com|io|us|markets)"
)
LIVE_MARKERS = re.compile(r"(?i)\b(PROD|PRODUCTION|LIVE|MAINNET)\b")
SECRET_ASSIGN = re.compile(r"(?i)\b\w*(SECRET|API_KEY|APIKEY|PRIVATE_KEY|PASSWORD|TOKEN)\w*\s*=\s*\S")
DESTRUCTIVE = re.compile(
    r"(?i)(\brm\s+-rf?\s+[/~]|\bgit\s+push\b.*--force|\bDROP\s+(TABLE|DATABASE)\b|\bTRUNCATE\s+TABLE\b|"
    r"\bmkfs\b|\bdd\s+if=)"
)
REMOTE_URL = re.compile(r"(?i)\b(https?|wss?)://(?!(127\.0\.0\.1|localhost|0\.0\.0\.0|\[::1\])(:|/|$))[\w.-]+")
SAFE_HOST_HINT = re.compile(r"(?i)(testnet|sandbox|paper|mock|example\.(com|org|net))")
AUDIT_CMD = re.compile(r"auditor_cli\.py\s+(trace|repro)\b")


def check(tool: str, inp: dict) -> str | None:
    if tool == "Bash":
        cmd = inp.get("command", "") or ""
        if DESTRUCTIVE.search(cmd):
            return "destructive command blocked by the Argus guard"
        if AUDIT_CMD.search(cmd):
            if LIVE_HOSTS.search(cmd) and not SAFE_HOST_HINT.search(cmd):
                return "audit run points at a live exchange/API host; use a testnet, sandbox or loopback mock"
            if SECRET_ASSIGN.search(cmd) or LIVE_MARKERS.search(cmd):
                return "audit run sets credentials or a PROD/LIVE marker; run against a sandbox environment"
        return None
    if tool in ("Write", "Edit"):
        path = (inp.get("file_path") or "").replace("\\", "/")
        if "/.audit/repros/" not in path and not path.startswith(".audit/repros/"):
            return None
        text = inp.get("content") or inp.get("new_string") or ""
        if LIVE_HOSTS.search(text) and not SAFE_HOST_HINT.search(text):
            return "reproduction references a live exchange/API host; reproductions may only use loopback"
        m = REMOTE_URL.search(text)
        if m and not SAFE_HOST_HINT.search(m.group(0)):
            return f"reproduction contains a remote URL ({m.group(0)}); use a loopback server or mock"
        if SECRET_ASSIGN.search(text):
            return "reproduction contains a credential assignment; reproductions must not carry secrets"
        if re.search(r"(?i)(\.env\b|credentials|keyring)", text):
            return "reproduction reads credentials or .env; build clients against a loopback server instead"
    return None


def main() -> int:
    try:
        payload = json.load(sys.stdin)
    except json.JSONDecodeError:
        return 0
    reason = check(payload.get("tool_name", ""), payload.get("tool_input") or {})
    if reason:
        print(f"Argus guard: {reason}", file=sys.stderr)
        return 2
    return 0


if __name__ == "__main__":
    sys.exit(main())
