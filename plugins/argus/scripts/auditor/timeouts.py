"""Parse durations out of source text: timeout arguments, deadline wrappers, sleeps.

Returns seconds as float, or None when no duration is recognizable. Units follow
each API's convention (JS `timeout:` and Kotlin `withTimeout(n)` are
milliseconds; Python, Ruby, PHP and Swift bare numbers are seconds).
"""
from __future__ import annotations

import re

_UNITS = {
    "ns": 1e-9, "nanos": 1e-9, "nanoseconds": 1e-9, "us": 1e-6, "micros": 1e-6, "microseconds": 1e-6,
    "ms": 1e-3, "millis": 1e-3, "milliseconds": 1e-3, "millisecond": 1e-3,
    "s": 1.0, "sec": 1.0, "secs": 1.0, "second": 1.0, "seconds": 1.0,
    "m": 60.0, "min": 60.0, "mins": 60.0, "minute": 60.0, "minutes": 60.0,
    "h": 3600.0, "hours": 3600.0, "hour": 3600.0,
}
NUM = r"(\d+(?:\.\d+)?|\.\d+)(?:_?(?:u64|u32|i64|f64|f32|L|l|f|d))?"

_PATTERNS: list[tuple[re.Pattern, object]] = [
    # Rust: Duration::from_secs(2), from_millis(500), from_secs_f64(1.5)
    (re.compile(rf"from_(secs|millis|micros|nanos)(?:_f(?:32|64))?\(\s*{NUM}"),
     lambda m: float(m.group(2)) * {"secs": 1, "millis": 1e-3, "micros": 1e-6, "nanos": 1e-9}[m.group(1)]),
    # Java/Kotlin: Duration.ofSeconds(10), ofMillis(500); Kotlin 5.seconds / 500.milliseconds
    (re.compile(rf"of(Seconds|Millis|Minutes|Nanos)\(\s*{NUM}"),
     lambda m: float(m.group(2)) * {"Seconds": 1, "Millis": 1e-3, "Minutes": 60, "Nanos": 1e-9}[m.group(1)]),
    (re.compile(rf"\b{NUM}\s*\.\s*(seconds|milliseconds|minutes|second|millisecond|minute)\b"),
     lambda m: float(m.group(1)) * _UNITS[m.group(2)]),
    # Java: (10, TimeUnit.SECONDS)
    (re.compile(rf"{NUM}\s*,\s*TimeUnit\.(SECONDS|MILLISECONDS|MINUTES|MICROSECONDS)"),
     lambda m: float(m.group(1)) * {"SECONDS": 1, "MILLISECONDS": 1e-3, "MINUTES": 60,
                                    "MICROSECONDS": 1e-6}[m.group(2)]),
    # C#: TimeSpan.FromSeconds(10)
    (re.compile(rf"From(Seconds|Milliseconds|Minutes)\(\s*{NUM}"),
     lambda m: float(m.group(2)) * {"Seconds": 1, "Milliseconds": 1e-3, "Minutes": 60}[m.group(1)]),
    # Go: 5 * time.Second, time.Second * 5, time.Duration(5) * time.Second, 500*time.Millisecond
    (re.compile(rf"{NUM}\s*\*\s*time\.(Second|Millisecond|Minute|Microsecond)"),
     lambda m: float(m.group(1)) * {"Second": 1, "Millisecond": 1e-3, "Minute": 60, "Microsecond": 1e-6}[m.group(2)]),
    (re.compile(rf"time\.(Second|Millisecond|Minute)\s*\*\s*(?:time\.Duration\()?{NUM}"),
     lambda m: float(m.group(2)) * {"Second": 1, "Millisecond": 1e-3, "Minute": 60}[m.group(1)]),
    (re.compile(r"time\.Duration\(\s*" + NUM + r"\s*\)\s*\*\s*time\.(Second|Millisecond|Minute)"),
     lambda m: float(m.group(1)) * {"Second": 1, "Millisecond": 1e-3, "Minute": 60}[m.group(2)]),
    # curl
    (re.compile(rf"CURLOPT_(?:CONNECT)?TIMEOUT_MS\s*,\s*{NUM}"), lambda m: float(m.group(1)) / 1000),
    (re.compile(rf"CURLOPT_(?:CONNECT)?TIMEOUT\s*,\s*{NUM}"), lambda m: float(m.group(1))),
    # JS: AbortSignal.timeout(1000)
    (re.compile(rf"AbortSignal\.timeout\(\s*{NUM}"), lambda m: float(m.group(1)) / 1000),
    # Go: a bare time.Second (lowest priority: `5 * time.Second` starts earlier and wins)
    (re.compile(r"\btime\.(Second|Millisecond|Minute)\b"),
     lambda m: {"Second": 1.0, "Millisecond": 1e-3, "Minute": 60.0}[m.group(1)]),
]

# Bare numbers after a timeout keyword; the unit depends on the language.
_KEYWORD = re.compile(
    rf"(?:\btimeout|\bread_timeout|\bopen_timeout|\btimeoutInterval\w*|\btotal|['\"]timeout['\"]|"
    rf"\bconnect_timeout|\bwithTimeout(?:OrNull)?|\bTimeout\.timeout|\bCancelAfter|\bwait_for\([^,()]*(?:\([^()]*\))?[^,()]*,)"
    rf"\s*(?:=>|[:=(,])?\s*\(?\s*{NUM}"
)
_MS_LANGS = {"javascript", "typescript"}


_POSITIONAL = re.compile(r"\b(wait_for|timeout_at|timeout)\(")


def _args(text: str, open_idx: int) -> list[str]:
    """Top-level arguments of the call whose '(' is at open_idx."""
    depth, cur, out = 0, "", []
    for ch in text[open_idx:]:
        if ch in "([{":
            depth += 1
            if depth == 1:
                continue
        elif ch in ")]}":
            depth -= 1
            if depth == 0:
                out.append(cur)
                return [a.strip() for a in out]
        if ch == "," and depth == 1:
            out.append(cur)
            cur = ""
        else:
            cur += ch
    return [a.strip() for a in out + [cur]]


def parse_duration(text: str, lang: str = "") -> float | None:
    """The first duration in `text`, in seconds."""
    if not text:
        return None
    best: tuple[int, float] | None = None
    # asyncio.wait_for(<anything>, 10): the deadline is a bare positional number.
    for m in _POSITIONAL.finditer(text):
        args = _args(text, m.end() - 1)
        for a in args[1:2] if m.group(1) == "wait_for" else args[:1]:
            if re.fullmatch(NUM, a):
                best = (m.start(), float(re.match(NUM, a).group(1)))
                break
        if best:
            break
    for rx, conv in _PATTERNS:
        m = rx.search(text)
        if m and (best is None or m.start() < best[0]):
            best = (m.start(), conv(m))
    m = _KEYWORD.search(text)
    if m and (best is None or m.start() < best[0]):
        v = float(m.group(1))
        kw = m.group(0)
        if lang in _MS_LANGS or re.search(r"withTimeout|CancelAfter", kw):
            v /= 1000.0
        best = (m.start(), v)
    return best[1] if best else None


def parse_default(text: str | None) -> float | None:
    """Rule-table defaults look like '30s (reqwest...)' or '300s total (...)'."""
    if not text:
        return None
    m = re.match(r"\s*(\d+(?:\.\d+)?)\s*(ms|s|min)\b", text)
    if not m:
        return None
    return float(m.group(1)) * {"ms": 1e-3, "s": 1.0, "min": 60.0}[m.group(2)]


def sleep_duration(text: str, lang: str = "") -> float | None:
    """Duration of a sleep call: time.sleep(0.5), thread::sleep(Duration::...), Thread.sleep(100)."""
    d = parse_duration(text, lang)
    if d is not None:
        return d
    m = re.search(rf"(?:\bsleep|\bSleep|\busleep|\bdelay)\s*\(\s*{NUM}", text)
    if not m:
        return None
    v = float(m.group(1))
    if lang in ("java", "kotlin", "scala", "csharp", "javascript", "typescript") or "delay(" in m.group(0):
        v /= 1000.0  # Thread.sleep / Thread.Sleep / delay take milliseconds
    elif "usleep" in m.group(0):
        v /= 1e6
    return v


def fmt(seconds: float | None) -> str:
    if seconds is None:
        return "unknown"
    if seconds == float("inf"):
        return "unbounded"
    if seconds < 1:
        return f"{seconds * 1000:.0f} ms"
    if seconds < 120:
        return f"{seconds:g} s"
    return f"{seconds / 60:.1f} min"
