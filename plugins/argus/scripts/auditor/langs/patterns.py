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
        # A literal SELECT with no WHERE/LIMIT (aggregates excluded), or `.fetchall()` -- except in a generic query
        # helper that takes its SQL as a parameter: it fetches whatever its caller asked for, so the caller that
        # wrote the SQL is the lead (see _GENERIC_SQL_HELPER).
        "python": _r(r"""["'](?i:\s*select)\b(?![^"']*(?i:\blimit\b|\bwhere\b|\bexists\b|\b(?:count|sum|max|min|avg)\s*\())"""
                     r"""[^"']*\b(?i:from)\s+\w+[^"']*["']|\.objects\.all\s*\(\s*\)\s*(?!\.)|\.fetchall\s*\(\s*\)"""),
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
    "sql-injection": ("high", "SQL text built by formatting values into it (f-string, %, +, format, template "
                              "literal). A caller-controlled value changes the query itself: rows leak, filters are "
                              "bypassed, tables are altered. Pass values as bind parameters.", {
        "python": _r(r"""\.(execute|executemany|executescript|raw|exec_driver_sql)\s*\(\s*(f(["'])(?:(?!\3).)*\{|"""
                     r"""(["'])(?:(?!\4).)*\4\s*(%\s*[\w(]|\+\s*\w|\.format\())|\btext\s*\(\s*f(["'])(?:(?!\6).)*\{|"""
                     # any helper handed an f-string that is SQL (`query(f"select ... {term}")`)
                     r"""\(\s*f(["'])\s*(?i:select|insert|update|delete|with)\b(?:(?!\7).)*\{"""),
        "javascript": _r(r"\.(query|execute|raw|\$queryRawUnsafe|\$executeRawUnsafe)\s*\(\s*(`[^`]*\$\{|['\"][^'\"]*['\"]\s*\+\s*\w)"),
        "typescript": _r(r"\.(query|execute|raw|\$queryRawUnsafe|\$executeRawUnsafe)\s*\(\s*(`[^`]*\$\{|['\"][^'\"]*['\"]\s*\+\s*\w)"),
        "java": _r(r"\.(executeQuery|executeUpdate|execute|createQuery|createNativeQuery|prepareStatement)\s*\(\s*\"[^\"]*\"\s*\+\s*\w"),
        "go": _r(r"\.(Query|QueryRow|Exec)(Context)?\s*\([^)]*(fmt\.Sprintf\(|\"[^\"]*\"\s*\+\s*\w)"),
    }),
    "backoff-without-jitter": ("low", "Retries back off but with no random jitter. Clients that failed together retry "
                                    "together, which turns a short outage into repeated load spikes. Add jitter.", {}),
}

# Same rule, different stakes: a dropped Python task can be garbage collected mid-run and its exception is
# only logged at exit, so it is worth an investigation, not just a note.
SEVERITY_BY_LANG = {("fire-and-forget-task", "python"): "medium"}

_ASSIGN_AWAIT = {
    "rust": re.compile(r"^\s*let\s+(?:mut\s+)?(\w+)\s*(?::[^=]+)?=\s*(.+?)\.await\s*\??\s*;\s*$"),
    "python": re.compile(r"^\s*(\w+)\s*=\s*await\s+(.+)$"),
    "javascript": re.compile(r"^\s*(?:const|let|var)\s+(\w+)\s*=\s*await\s+(.+?);?\s*$"),
    "typescript": re.compile(r"^\s*(?:const|let|var)\s+(\w+)\s*=\s*await\s+(.+?);?\s*$"),
}
_SEQ_MSG = ("Consecutive awaits where the second does not use the first's result run one after the other. "
            "If the calls are independent, start them together (join/gather/Promise.all/WhenAll) to cut the wait to the slower one.")
# Interpolations that build SQL structure from trusted parts (`in ({placeholders})`), not values.
_SAFE_SQL_PART = re.compile(r"(?i)^(placeholders?|qmarks?|marks|params?_sql|binds?|columns?|cols|fields|table\w*|"
                            r"order_by|sort_col|where_sql|clause|n)$")
_GENERIC_SQL_HELPER = re.compile(r"[(,]\s*(sql|query|stmt|statement|sql_text)\s*[,:)=]")
_WALLCLOCK_SET = {
    "python": re.compile(r"(?:^|\s)((?:self\.)?\w+)\s*=\s*time\.time\s*\(\s*\)"),
    "javascript": re.compile(r"(?:^|\s)(?:const|let|var)?\s*((?:this\.)?\w+)\s*=\s*Date\.now\s*\(\s*\)"),
    "typescript": re.compile(r"(?:^|\s)(?:const|let|var)?\s*((?:this\.)?\w+)\s*=\s*Date\.now\s*\(\s*\)"),
}
_MODULE_REGEX = {
    "python": re.compile(r"^([A-Z_][A-Z0-9_]*)\s*=\s*re\.compile\s*\((.+)", re.M),
    "javascript": re.compile(r"^(?:export\s+)?(?:const|let|var)\s+(\w+)\s*=\s*(/.+/[a-z]*|new RegExp\(.+)", re.M),
    "typescript": re.compile(r"^(?:export\s+)?(?:const|let|var)\s+(\w+)\s*=\s*(/.+/[a-z]*|new RegExp\(.+)", re.M),
}
_RETRYISH = re.compile(r"(?i)\b(retry|retries|attempt|attempts|backoff)\b")
_BACKOFF = re.compile(r"(?i)backoff|\*\*\s*\w|\bpow\s*\(|<<|exponential|\*=\s*2")
_JITTER = re.compile(r"(?i)jitter|random|\brand\b|rng|uniform")


def _lines(body) -> list[tuple[int, str]]:
    base = body.start_point[0] + 1
    return [(base + i, ln) for i, ln in enumerate(text(body).split("\n")) if not COMMENT.match(ln)]


def _signature(fn_node, body) -> list[tuple[int, str]]:
    """The declaration up to the body: parameter and return types count (`unit_price: float`)."""
    if body is None or body.start_byte <= fn_node.start_byte:
        return []
    head = fn_node.text[: body.start_byte - fn_node.start_byte].decode("utf8", "replace")
    base = fn_node.start_point[0] + 1
    return [(base + i, ln) for i, ln in enumerate(head.split("\n"))]


def _module_regexes(spec, fn_node) -> dict[str, str]:
    """Module-level regex constants (`COUPON = re.compile(r"...")`): name -> pattern text."""
    rx = _MODULE_REGEX.get(spec.name)
    if rx is None:
        return {}
    root = fn_node
    while root.parent is not None:
        root = root.parent
    return {m.group(1): m.group(2) for m in rx.finditer(text(root))}


def _sql_values_interpolated(line: str) -> bool:
    names = re.findall(r"\{([^{}:!]+)[^{}]*\}", line)
    return any(not _SAFE_SQL_PART.match(n.strip().split(".")[-1]) for n in names) if names else True


def pattern_rules(spec, fn_node, body, is_async) -> list[HookHit]:
    lines = _lines(body)
    sig = _signature(fn_node, body)
    hits: list[HookHit] = []
    for rule, (sev, msg, table) in RULES.items():
        rx = table.get(spec.name, table.get("*"))
        if rx is None:
            continue
        for ln, s in (sig + lines if rule == "float-money" else lines):
            if len(s) < 400 and rx.search(s):
                if rule == "sql-injection" and not _sql_values_interpolated(s):
                    continue  # only structure (`in ({placeholders})`) is formatted in; values are bound
                if (rule == "unbounded-query" and ".fetchall" in s and "select" not in s.lower()
                        and any(_GENERIC_SQL_HELPER.search(h) for _, h in sig)):
                    continue  # a helper running its caller's SQL
                hits.append(HookHit(rule, SEVERITY_BY_LANG.get((rule, spec.name), sev), ln, msg))
                break  # one lead per function and rule: enough to send the investigator there
    # A module-level regex is matched where it is used: that is the function to investigate.
    redos = RULES["redos-regex"][2]["*"]
    for name, pat in _module_regexes(spec, fn_node).items():
        if not redos.search(pat[:400]):
            continue
        use = next(((ln, s) for ln, s in lines if re.search(rf"\b{re.escape(name)}\b", s)), None)
        if use and not any(h.rule == "redos-regex" for h in hits):
            hits.append(HookHit("redos-regex", RULES["redos-regex"][0], use[0],
                                f"`{name}` ({pat.strip()[:60]}) " + RULES["redos-regex"][1]))
    # Wall clock read into a variable, then subtracted: `now = time.time()` ... `last + interval - now`.
    wset = _WALLCLOCK_SET.get(spec.name)
    if wset is not None and not any(h.rule == "wallclock-interval" for h in hits):
        names = {m.group(1) for _, s in lines for m in [wset.search(s)] if m}
        for ln, s in lines:
            if any(re.search(rf"-\s*{re.escape(v)}\b|\b{re.escape(v)}\s*-(?!=)", s) for v in names):
                hits.append(HookHit("wallclock-interval", RULES["wallclock-interval"][0], ln,
                                    RULES["wallclock-interval"][1]))
                break
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
