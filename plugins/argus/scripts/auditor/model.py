"""Language-neutral data model shared by extraction, resolution and analysis."""
from __future__ import annotations

from dataclasses import dataclass, field


@dataclass
class Call:
    name: str
    raw: str  # callee text as written, whitespace-normalized
    path: str  # import-resolved dotted path, including the name
    receiver: str  # normalized receiver text ("" for bare calls)
    kind: str  # "ident" | "path" | "method"
    line: int
    awaited: bool
    context: str  # "async" | "sync" | "offloaded"
    loop_depth: int
    loop_kinds: list[str]
    has_timeout: bool
    snippet: str
    self_call: bool = False
    argc: int | None = None  # None: unknown
    stmt_line: int = 0  # first line of the enclosing statement
    timeout_s: float | None = None  # parsed deadline/timeout value, when has_timeout
    tail: str = ""  # statement text right after this call (what happens to its result)


@dataclass
class LoopInfo:
    """A loop, described enough to tell retry loops from iteration."""
    line: int
    end_line: int
    kind: str  # "forever" | "counted" | "conditional" | "collection"
    bound: int | None  # iteration count when it is a literal (or a literal constant)
    handles_errors: bool  # try/except, match Err, `err != nil`, .catch ... inside the body
    sleep_s: float | None  # delay between iterations when a sleep call is present (0.0 if unparsed)
    exponential: bool  # the delay grows (2 ** n, pow, <<, backoff helpers)
    exits: bool = False  # break/return inside: stops once something succeeds
    retryish: bool = False  # attempt/retry/tries/backoff naming in the loop
    policy_bound: bool = False  # bounded by a counter check or a retry-policy object inside the body


@dataclass
class Function:
    id: str
    lang: str
    name: str
    qualname: str
    file: str
    line: int
    end_line: int
    module: tuple[str, ...]
    container: str | None
    is_async: bool
    is_entry: bool
    arity: tuple[int, float] | None = None  # (min, max) explicit args; None: unknown
    takes_self: bool = False  # has an explicit self parameter (Rust, Python)
    private: bool = False  # not callable from other modules (Rust non-pub, Go lowercase, `private`)
    max_loop_depth: int = 0
    calls: list[Call] = field(default_factory=list)
    loops: list[LoopInfo] = field(default_factory=list)
    # Function-level retry (tenacity/backoff decorators, @Retryable): (attempts or None=unbounded, backoff)
    retry: tuple[int | None, str] | None = None


@dataclass
class FileCtx:
    rel: str
    lang: str
    imports: dict[str, str]  # alias -> dotted path; keys starting with "\0" are unaliased imports
    client_timeout: bool
    functions: list[str] = field(default_factory=list)  # function ids, in source order
    client_timeout_s: float | None = None  # value of a client-level timeout configured in this file
    untimed_client: bool = False  # this file builds an HTTP client with no timeout configured

    def imported(self, rx) -> bool:
        return any(rx.search(v) for v in self.imports.values())


@dataclass
class Edge:
    caller: str
    callee: str
    line: int
    awaited: bool
    context: str
    loop_depth: int
    loop_kinds: list[str]
    confidence: str  # "exact" | "scip" | "unique" | "name"
    timeout_s: float | None = None  # deadline the caller wraps around this call


@dataclass
class Boundary:
    function: str
    kind: str
    category: str  # net | db | fs | sleep | process | wait | runtime
    blocking: bool
    call: Call
    confidence: str
    default_timeout: str | None
    default_client: bool = False
    client_level: bool = True  # a timeout could be configured on the client object elsewhere


@dataclass
class Finding:
    rule: str
    severity: str
    confidence: str
    lang: str
    function: str
    file: str
    line: int
    message: str
    chain: list[str] = field(default_factory=list)
    reached_from: list[str] = field(default_factory=list)


@dataclass
class HookHit:
    """A language-specific finding produced during extraction."""
    rule: str
    severity: str
    line: int
    message: str
    confidence: str = "heuristic"
