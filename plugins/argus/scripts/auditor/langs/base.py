"""Building blocks for language specs: rules, the spec itself, and tree helpers."""
from __future__ import annotations

import re
from dataclasses import dataclass, field
from typing import Callable, Iterator

from tree_sitter import Node

NET, DB, FS, SLEEP, PROCESS, WAIT, RUNTIME = "net", "db", "fs", "sleep", "process", "wait", "runtime"

BASE_COMMON_METHODS = frozenset({
    "new", "get", "set", "insert", "remove", "push", "pop", "len", "is_empty", "clone",
    "iter", "iter_mut", "into_iter", "map", "unwrap", "expect", "to_string", "into", "from",
    "as_ref", "lock", "read", "write", "send", "recv", "call", "next", "collect", "contains",
    "extend", "update", "run", "start", "stop", "build", "default", "fmt", "eq", "cmp", "hash",
    "drop", "join", "execute", "request", "json", "text", "ok", "err", "and_then", "sum",
    "append", "add", "put", "keys", "values", "items", "format", "split", "strip", "replace",
    "toString", "equals", "hashCode", "length", "size", "forEach", "filter", "reduce", "then",
    "catch", "log", "info", "debug", "warn", "error", "close", "open", "emit", "on", "apply",
    "bind", "Add", "ToString", "Equals", "Where", "Select", "ToList", "Count", "Any", "First",
    "String", "Error", "Close", "Get", "Set", "Printf", "Println", "Sprintf", "Errorf",
    "print", "println", "printf", "init", "__init__", "setUp", "main", "handle", "process",
    "find", "save", "delete", "create", "exec", "query", "reset", "clear", "flush", "finish",
    "cancel", "poll", "wait", "abort", "tick", "inc", "resolve", "parse", "validate", "load",
    "render", "encode", "decode", "serialize", "deserialize", "to_owned", "as_str", "borrow",
})


@dataclass(frozen=True)
class Rule:
    """Classifies an unresolved call as an I/O boundary.

    `path` matches the import-resolved dotted callee (e.g. `requests.get`).
    `methods` matches method names on receivers of unknown type and needs
    `requires_import` to have matched somewhere in the file.
    """
    kind: str
    category: str
    path: re.Pattern | None = None
    methods: frozenset[str] | None = None
    requires_import: re.Pattern | None = None
    name: re.Pattern | None = None  # matches the called name (any call kind)
    receiver: re.Pattern | None = None  # matches the normalized receiver text
    text: re.Pattern | None = None  # matches the statement text
    blocking: bool = False  # blocks the calling thread (only when not awaited)
    awaited: bool | None = None  # True: only if awaited, False: only if not, None: either
    default_timeout: str | None = None  # None: the library has no default timeout
    exclude_names: re.Pattern | None = None
    conf: str | None = None
    default_client: bool = False  # uses a global client, so file-level client timeouts don't apply
    client_level: bool = True  # False when the library only supports per-request timeouts

    def confidence(self) -> str:
        if self.conf:
            return self.conf
        return "exact" if self.path is not None and self.receiver is None else "heuristic"


def _rx(v):
    return re.compile(v) if isinstance(v, str) else v


def R(kind, category, path=None, methods=None, imp=None, name=None, receiver=None, text=None, **kw) -> Rule:
    return Rule(
        kind=kind,
        category=category,
        path=_rx(path),
        methods=frozenset(methods) if methods else None,
        requires_import=_rx(imp),
        name=_rx(name),
        receiver=_rx(receiver),
        text=_rx(text),
        **kw,
    )


Hook = Callable[["LangSpec", Node, Node, bool], list]  # (spec, fn_node, body, is_async) -> [HookHit]


@dataclass
class LangSpec:
    name: str
    grammar: str
    extensions: tuple[str, ...]
    function_types: frozenset[str]
    call_types: frozenset[str]
    loop_types: frozenset[str]
    container_types: frozenset[str] = frozenset()
    module_types: frozenset[str] = frozenset()
    lambda_types: frozenset[str] = frozenset()
    named_lambda_parents: frozenset[str] = frozenset()
    await_types: frozenset[str] = frozenset()
    async_scope_types: frozenset[str] = frozenset()  # always async (Rust `async {}`)
    attribute_types: frozenset[str] = frozenset({
        "attribute_item", "attribute", "attribute_list", "decorator", "annotation",
        "marker_annotation",
    })
    has_async: bool = False
    async_header: re.Pattern | None = None
    sep: str = "."
    self_names: frozenset[str] = frozenset({"self", "this", "$this", "Self"})
    capital_is_type: bool = True
    iter_methods: frozenset[str] = frozenset()
    offload: re.Pattern | None = None  # matched against "<callee path> <call text>"
    async_wrappers: re.Pattern | None = None  # lambdas passed to these run async
    await_combinators: re.Pattern | None = None
    timeout_call: re.Pattern | None = None
    timeout_text: re.Pattern | None = None
    client_timeout: re.Pattern | None = None
    entry: re.Pattern | None = None  # matched against attributes + header
    startup: re.Pattern | None = None  # runs once before serving traffic (startup hooks): matched like `entry`
    rules: list[Rule] = field(default_factory=list)
    imports: Callable[[Node], dict[str, str]] = lambda root: {}
    hooks: list[Hook] = field(default_factory=list)
    common_methods: frozenset[str] = BASE_COMMON_METHODS
    module_of: Callable | None = None  # (rel: PurePath, root_name) -> tuple[str, ...]
    receiver_info: Callable | None = None  # fn_node -> (self_name, type_name), e.g. Go receivers
    file_is_module: bool = True  # False where one file holds one class (Java, C#, ...)
    arity_mode: str = "strict"  # "strict" | "no_min" (JS: missing args allowed) | "no_max" (PHP: extras allowed)
    private_header: re.Pattern | None = re.compile(r"\b(private|fileprivate)\b")
    private_of: Callable | None = None  # (fn_node, name, header) -> bool; overrides private_header
    macro_types: frozenset[str] = frozenset()  # unparsed token trees scanned for calls (Rust macros)
    macro_awaits: re.Pattern | None = None  # macros whose inner futures are awaited (select!, join!)
    stall_phrase: str = "the calling thread"
    scip_indexer: str | None = None  # key into scip.INDEXERS


# --- tree helpers ------------------------------------------------------------

def text(node: Node | None) -> str:
    return node.text.decode("utf8", "replace") if node is not None else ""


def walk(node: Node, stop: Callable[[Node], bool] = lambda n: False) -> Iterator[Node]:
    """Preorder traversal. Children for which `stop` is true are not entered."""
    stack = [node]
    while stack:
        n = stack.pop()
        yield n
        for c in reversed(n.named_children):
            if not stop(c):
                stack.append(c)


def split_top(s: str, sep: str = ",") -> list[str]:
    parts, depth, cur = [], 0, ""
    for ch in s:
        depth += ch in "{(["
        depth -= ch in "})]"
        if ch == sep and depth == 0:
            parts.append(cur)
            cur = ""
        else:
            cur += ch
    parts.append(cur)
    return [p.strip() for p in parts if p.strip()]


def expand_braced(spec: str, sep: str, prefix: str = "", alias_kw: str = "as") -> list[tuple[str, str]]:
    """`a::{b, c::{self, D as E}}` -> [(b, a.b), (c, a.c), (E, a.c.D)] (always dotted output)."""
    spec = re.sub(rf"\s+{alias_kw}\s+", "@", spec.strip())
    spec = re.sub(r"\s+", "", spec)
    if spec.endswith("}") and "{" in spec:
        i = spec.index("{")
        base = spec[:i].rstrip(sep[-1]).rstrip(sep)
        out = []
        for part in split_top(spec[i + 1:-1]):
            if part == "self":
                out.append((base.split(sep)[-1], (prefix + base).replace(sep, ".")))
            else:
                out += expand_braced(part, sep, f"{prefix}{base}{sep}" if base else prefix, alias_kw)
        return out
    path, _, alias = spec.partition("@")
    full = (prefix + path).replace(sep, ".")
    if full.endswith("*") or full.endswith("_") or alias == "_":
        return []
    return [(alias or full.split(".")[-1], full)]
