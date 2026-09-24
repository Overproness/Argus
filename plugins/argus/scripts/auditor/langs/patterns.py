"""Source-pattern rules shared by every language: one hook, one table of regexes per rule.

These are heuristics over a function's text (comment lines skipped), so every hit is `heuristic`
confidence and worded as a lead. The investigator decides whether it is real.
"""
from __future__ import annotations

import re

from ..model import HookHit
from .base import text

MONEY = r"(?:price|amount|balance|money|cost|fee|salary|pnl|notional|payment|invoice)"
COMMENT = re.compile(r"^\s*(//|#|\*|/\*|--)")


def _r(p: str) -> re.Pattern:
    return re.compile(p.replace("(?i)", ""), re.I) if p.startswith("(?i)") else re.compile(p)


# rule -> (severity, message, {language: pattern}); "*" applies to every language without its own entry
RULES: dict[str, tuple[str, str, dict[str, re.Pattern]]] = {
    "unbounded-channel": ("low", "Unbounded queue or channel: a producer faster than its consumer grows memory "
                                 "without limit and never pushes back. Bound it and decide what happens when full.", {
        "rust": _r(r"\bunbounded_channel\s*\(|\bunbounded\s*\(\s*\)|\bstd::sync::mpsc::channel\s*(::<[^>]*>)?\s*\("),
        "python": _r(r"\b(asyncio\.)?(Priority|Lifo)?Queue\s*\(\s*(maxsize\s*=\s*0\s*)?\)|\bcollections\.deque\s*\(\s*\)"),
        "java": _r(r"new\s+(LinkedBlockingQueue|LinkedBlockingDeque|ConcurrentLinkedQueue|PriorityBlockingQueue)\s*(<[^>]*>)?\s*\(\s*\)"),
        "go": _r(r"\bmake\s*\(\s*chan\b[^)]*,\s*(1\d{4,}|[2-9]\d{4,})\s*\)"),
    }),
    "float-money": ("medium", "Floating point holds a money-like value. Binary floats cannot represent most decimal "
                              "amounts, so sums drift and comparisons fail. Use integer minor units or a decimal type.", {
        "*": _r(rf"(?i)\b\w*{MONEY}\w*\s*(:|=)\s*(?:&?(?:mut\s+)?)?(f32|f64|float32|float64|float|double|Double|Float)\b"
                rf"|\b(f32|f64|float32|float64|float|double|Double|Float)\s+\w*{MONEY}\w*\b"
                rf"|\b\w*{MONEY}\w*\s*=\s*float\("),
    }),
    "wallclock-interval": ("medium", "Elapsed time measured with the wall clock. It jumps when NTP or a person changes "
                                     "the clock, so intervals can be negative or huge. Use the monotonic clock.", {
        "rust": _r(r"SystemTime::now\s*\(\s*\)[^;]*\.(elapsed|duration_since)\s*\(|\.duration_since\s*\(\s*(std::time::)?UNIX_EPOCH"
                   r"(?!.*(as_secs|as_millis|timestamp))"),
        "python": _r(r"\btime\.time\s*\(\s*\)\s*-|-\s*\w*(start|began|begin|t0|started)\w*\b.*\btime\.time\s*\("),
        "javascript": _r(r"\bDate\.now\s*\(\s*\)\s*-|-\s*\w*(start|began|begin|t0|started)\w*\b.*\bDate\.now\s*\("),
        "typescript": _r(r"\bDate\.now\s*\(\s*\)\s*-|-\s*\w*(start|began|begin|t0|started)\w*\b.*\bDate\.now\s*\("),
        "java": _r(r"System\.currentTimeMillis\s*\(\s*\)\s*-|-\s*\w*(start|began|begin|t0|started)\w*\b.*currentTimeMillis"),
    }),
    "panic-on-external-data": ("medium", "Parsing or decoding outside data and unwrapping the result. Malformed input "
                                         "from a peer, file or API crashes the task (or the whole process). Handle the error.", {
        "rust": _r(r"\.parse\s*(::<[^>]*>)?\s*\(\s*\)\s*\.(unwrap|expect)\s*\(|serde_json::from_\w+\s*(::<[^>]*>)?\s*\([^;]*\)\s*\.(unwrap|expect)\s*\("
                   r"|\.(json|text|bytes)\s*(::<[^>]*>)?\s*\(\s*\)\s*\.await\s*\.(unwrap|expect)\s*\("),
        "go": _r(r"\b\w+,\s*_\s*:?=\s*(strconv\.\w+|json\.Unmarshal)\b|json\.Unmarshal\([^)]*\)\s*$"),
    }),
    "redos-regex": ("medium", "A regular expression with a repeated group that itself contains a repeat. On crafted "
                              "input a backtracking engine takes exponential time. Rewrite it or use a linear-time engine.", {
        "*": _r(r"""["'/]\S*\((?:\?:)?[^()"']*[+*][^()"']*\)[+*{]"""),
    }),
    "unbounded-query": ("low", "Loads every row of a table or collection into memory at once. It works on the "
                               "test data and fails when the table grows. Paginate or stream.", {
        "python": _r(r"\.fetchall\s*\(\s*\)|\.objects\.all\s*\(\s*\)\s*(?!\.)|SELECT\s+\*\s+FROM\s+\w+\s*['\"]"),
        "rust": _r(r"\.fetch_all\s*\(|\.find\s*\(\s*doc!\s*\{\s*\}"),
        "java": _r(r"\.findAll\s*\(\s*\)|\.getResultList\s*\(\s*\)"),
        "javascript": _r(r"\.find\s*\(\s*\{\s*\}\s*\)|\.findMany\s*\(\s*\)|\.findAll\s*\(\s*\)"),
        "typescript": _r(r"\.find\s*\(\s*\{\s*\}\s*\)|\.findMany\s*\(\s*\)|\.findAll\s*\(\s*\)"),
    }),
    "fire-and-forget-task": ("low", "A spawned task whose handle is dropped. Nothing waits for it, so its errors and "
                                    "panics vanish, shutdown does not wait for it, and (Python) it can be garbage collected mid-run.", {
        "rust": _r(r"^\s*(tokio::)?(task::)?spawn\s*\("),
        "python": _r(r"^\s*(asyncio\.)?(create_task|ensure_future)\s*\("),
        "java": _r(r"new\s+Thread\s*\([^;]*\)\s*\.start\s*\(\s*\)"),
    }),
    "backoff-without-jitter": ("low", "Retries back off but with no random jitter. Clients that failed together retry "
                                    "together, which turns a short outage into repeated load spikes. Add jitter.", {}),
}

_ASSIGN_AWAIT = {
    "rust": re.compile(r"^\s*let\s+(?:mut\s+)?(\w+)\s*(?::[^=]+)?=\s*(.+?)\.await\s*\??\s*;\s*$"),
    "python": re.compile(r"^\s*(\w+)\s*=\s*await\s+(.+)$"),
    "javascript": re.compile(r"^\s*(?:const|let|var)\s+(\w+)\s*=\s*await\s+(.+?);?\s*$"),
    "typescript": re.compile(r"^\s*(?:const|let|var)\s+(\w+)\s*=\s*await\s+(.+?);?\s*$"),
}
_SEQ_MSG = ("Consecutive awaits where the second does not use the first's result run one after the other. "
            "If the calls are independent, start them together (join/gather/Promise.all/WhenAll) to cut the wait to the slower one.")
_RETRYISH = re.compile(r"(?i)\b(retry|retries|attempt|attempts|backoff)\b")
_BACKOFF = re.compile(r"(?i)backoff|\*\*\s*\w|\bpow\s*\(|<<|exponential|\*=\s*2")
_JITTER = re.compile(r"(?i)jitter|random|\brand\b|rng|uniform")


def _lines(body) -> list[tuple[int, str]]:
    base = body.start_point[0] + 1
    return [(base + i, ln) for i, ln in enumerate(text(body).split("\n")) if not COMMENT.match(ln)]


def pattern_rules(spec, fn_node, body, is_async) -> list[HookHit]:
    lines = _lines(body)
    hits: list[HookHit] = []
    for rule, (sev, msg, table) in RULES.items():
        rx = table.get(spec.name, table.get("*"))
        if rx is None:
            continue
        for ln, s in lines:
            if len(s) < 400 and rx.search(s):
                hits.append(HookHit(rule, sev, ln, msg))
                break  # one lead per function and rule: enough to send the investigator there
    rx = _ASSIGN_AWAIT.get(spec.name)
    if rx is not None and is_async:
        prev = None
        for ln, s in lines:
            m = rx.match(s)
            if m and prev is not None and not re.search(rf"\b{re.escape(prev[1])}\b", m.group(2)):
                hits.append(HookHit("sequential-awaits", "low", prev[0], _SEQ_MSG))
                break
            prev = (ln, m.group(1)) if m else None
    src = text(body)
    if _RETRYISH.search(src) and _BACKOFF.search(src) and not _JITTER.search(src):
        hits.append(HookHit("backoff-without-jitter", "low", body.start_point[0] + 1, RULES["backoff-without-jitter"][1]))
    return hits
