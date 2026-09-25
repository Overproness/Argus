"""Generic tree-sitter extraction driven by a LangSpec.

Grammars differ in node names, so everything here goes through the spec or
through text-level normalization that works across grammars (callee text,
statement text, declaration headers).
"""
from __future__ import annotations

import re
from pathlib import Path, PurePath

from tree_sitter import Node
from tree_sitter_language_pack import get_parser

from .langs.base import LangSpec, split_top, text, walk
from .model import Call, FileCtx, Function, HookHit, LoopInfo
from .timeouts import parse_duration, sleep_duration

BLOCKISH = frozenset({
    "block", "statement_block", "compound_statement", "statements", "body_statement",
    "declaration_list", "function_body", "statement_list", "class_body", "source_file",
    "program", "module", "do_block", "template_body", "field_declaration_list",
    "control_structure_body", "constructor_body", "switch_block", "match_block",
})
BODY_TYPES = ("function_body", "block", "compound_statement", "statement_block", "body_statement")
LOOP_BODIES = frozenset({"statements", "control_structure_body", "block", "compound_statement", "statement_block",
                         "body_statement", "do_block"})
NAME_TYPES = ("identifier", "field_identifier", "qualified_identifier", "destructor_name",
              "operator_name", "scoped_identifier")
CONTAINER_NAME_TYPES = ("type_identifier", "identifier", "simple_identifier", "constant", "name",
                        "user_type", "namespace_identifier", "scope_resolution")
NON_CALLEE_KIDS = ("type_arguments", "type_argument_list", "arguments", "argument_list", "comment")
TEST_MODULES = {"test", "tests"}
_GROUPS = [re.compile(p) for p in (r"\([^()]*\)", r"\[[^\[\]]*\]", r"<[^<>()]*>", r"\{[^{}]*\}")]
_parsers: dict = {}


def parser(grammar: str):
    if grammar not in _parsers:
        _parsers[grammar] = get_parser(grammar)
    return _parsers[grammar]


def normalize(raw: str) -> str:
    """Callee text -> dotted path without whitespace, arguments, indexes or generics."""
    s = re.sub(r"\s+", "", raw)
    for a, b in (("!!.", "."), ("?.", "."), ("&.", "."), ("->", "."), ("::", "."), ("\\", ".")):
        s = s.replace(a, b)
    prev = None
    while prev != s:
        prev = s
        for rx in _GROUPS:
            s = rx.sub("", s)
    return re.sub(r"\.+", ".", s).strip(".")


def canon(norm: str, imports: dict[str, str]) -> str:
    segs = norm.split(".") if norm else []
    if segs and segs[0] in imports:
        segs = imports[segs[0]].split(".") + segs[1:]
    return ".".join(segs)


def default_module(rel: PurePath, file_is_module: bool = True) -> tuple[str, ...]:
    parts = list(rel.with_suffix("").parts)
    if "src" in parts:
        parts = parts[len(parts) - parts[::-1].index("src"):]
    if len(parts) > 2 and parts[0] in ("main", "test") and parts[1] == "java":
        parts = parts[2:]
    stem = parts.pop() if parts else None
    if file_is_module and stem not in (None, "__init__", "index", "mod", "main", "lib"):
        parts.append(stem)
    return tuple(parts)


def _last_leaf(n: Node) -> Node:
    while True:
        if n.type == "generic_function":
            n = n.child_by_field_name("function") or n
        kids = [c for c in n.named_children if c.type not in NON_CALLEE_KIDS]
        if not kids:
            return n
        n = kids[-1]


def callee(call: Node) -> tuple[str, Node | None, set[int], bool]:
    """-> (raw callee text, node holding the called name, ids of callee nodes, is scoped)."""
    fn_node = call.child_by_field_name("function")
    if fn_node is None:
        nm = call.child_by_field_name("name") or call.child_by_field_name("method")
        if nm is not None:
            recv = (call.child_by_field_name("object") or call.child_by_field_name("receiver")
                    or call.child_by_field_name("scope"))
            raw = f"{text(recv)}.{text(nm)}" if recv is not None else text(nm)
            scoped = call.type == "scoped_call_expression" or (
                recv is not None and recv.type == "scope_resolution")
            ids = {nm.id} | ({recv.id} if recv is not None else set())
            return raw, nm, ids, scoped
        named = call.named_children
        fn_node = named[0] if named else None
    if fn_node is None:
        return "", None, set(), False
    return text(fn_node), _last_leaf(fn_node), {fn_node.id}, False


def _body(node: Node) -> Node | None:
    b = node.child_by_field_name("body")
    if b is not None:
        return b
    return next((c for c in node.named_children if c.type in BODY_TYPES), None)


def _header(node: Node, body: Node | None) -> str:
    if body is None:
        return text(node).split("\n", 1)[0]
    return node.text[: body.start_byte - node.start_byte].decode("utf8", "replace")


def _awaited(spec: LangSpec, node: Node) -> bool:
    p = node.parent
    while p is not None and p.type == "parenthesized_expression":
        p = p.parent
    return p is not None and p.type in spec.await_types


PARAM_CONTAINERS = ("parameters", "formal_parameters", "parameter_list", "function_value_parameters",
                    "method_parameters")
ARG_CONTAINERS = ("arguments", "argument_list", "value_arguments")
SPREAD_ARGS = ("spread_element", "splat_argument", "list_splat", "dictionary_splat", "hash_splat_argument",
               "block_argument", "spread_argument", "variadic_unpacking")
_SELF_PARAM = re.compile(r"^(&\s*('\w+\s+)?(mut\s+)?|mut\s+)?self\b|^(self|cls)\s*(:.*)?$")
_DEFAULT = re.compile(r"(?<![=!<>])=(?![=>])|^\w+\?\s*:")
_VARIADIC = re.compile(r"^\s*(\*|\.\.\.|params\s|vararg\s)|\.\.\.")


def _split_params(s: str) -> list[str]:
    parts, depth, cur, prev = [], 0, "", ""
    for ch in s:
        if ch in "([{<":
            depth += 1
        elif ch in ")]}" or (ch == ">" and prev not in "-="):
            depth -= 1
        if ch == "," and depth == 0:
            parts.append(cur)
            cur = ""
        else:
            cur += ch
        prev = ch
    parts.append(cur)
    return [p.strip() for p in parts if p.strip()]


def _arity(spec: LangSpec, node: Node, in_container: bool) -> tuple[tuple[int, float] | None, bool]:
    """-> ((min, max) explicit arguments or None, takes_self)."""
    plist = node.child_by_field_name("parameters")
    if plist is None:
        plist = next((c for c in node.named_children if c.type in PARAM_CONTAINERS), None)
    d = node.child_by_field_name("declarator")  # C/C++: int f(int a)
    while plist is None and d is not None:
        plist = d.child_by_field_name("parameters")
        d = d.child_by_field_name("declarator")
    if plist is not None:
        inner = text(plist).strip()
        if inner[:1] in "([|" and inner[-1:] in ")]|":
            inner = inner[1:-1]
        segs = _split_params(inner)
    else:
        return None, False
    if segs == ["void"]:
        segs = []
    takes_self = False
    if segs and _SELF_PARAM.match(segs[0]) and (spec.name == "rust" or (spec.name == "python" and in_container)):
        segs, takes_self = segs[1:], True
    segs = [s for s in segs if s not in ("*", "/")]  # py separators
    variadic = any(_VARIADIC.search(s) for s in segs)

    def optional(s):
        return bool(_DEFAULT.search(s))

    required = sum(1 for s in segs if not optional(s) and not _VARIADIC.search(s))
    lo, hi = required, (float("inf") if variadic else len(segs))
    if spec.arity_mode == "no_min":
        lo = 0
    elif spec.arity_mode == "no_max":
        hi = float("inf")
    return (lo, hi), takes_self


def _argc(call: Node) -> int | None:
    args = call.child_by_field_name("arguments")
    if args is None:
        args = next((c for c in call.named_children if c.type in ARG_CONTAINERS + ("call_suffix",)), None)
    if args is None:
        return 0 if call.type in ("method_invocation", "invocation_expression") else None
    if args.type not in ARG_CONTAINERS:
        return 1  # `f(x for x in y)`
    kids = [c for c in args.named_children if c.type != "comment"]
    if any(c.type in SPREAD_ARGS or text(c).lstrip().startswith(("...", "*")) for c in kids):
        return None
    return len(kids)


_MACRO_CALL = re.compile(
    r"(?<![\w:])((?:[A-Za-z_]\w*\s*(?:::|\.)\s*)*[A-Za-z_]\w*)\s*(?:::\s*<[^<>]*>\s*)?\("
)
_NOT_CALLS = {"if", "while", "match", "for", "return", "Some", "Ok", "Err", "Box", "move", "in", "as", "fn"}


def _matching_paren(s: str, i: int) -> int:
    depth = 0
    for j in range(i, len(s)):
        depth += s[j] == "("
        depth -= s[j] == ")"
        if depth == 0:
            return j
    return len(s) - 1


_FOREVER = re.compile(r"\s*(while\s*\(?\s*(true|True|1)\s*\)?\s*:?|for\s*(\(\s*;\s*;\s*\))?|loop|repeat)\s*")


# HTTP clients constructed with no configuration at all (so no timeout either).
_UNTIMED_CLIENT = re.compile(
    r"\bClient::new\(\)|\bClient\(\s*\)|\bnewHttpClient\(\)|\bnew\s+\w*HttpClient\(\s*\)|\bOkHttpClient\(\s*\)|"
    r"\bhttp\.Client\{\s*\}|\bClientSession\(\s*\)|\bSession\(\s*\)|\baxios\.create\(\s*\)|\bFaraday\.new\b(?!\s*\()|"
    r"\bnew\s+(\\?GuzzleHttp\\)?Client\(\s*\)"
)
_TIMEOUT_WITH = re.compile(r"\b(asyncio\.timeout|timeout_at|async_timeout\.timeout|move_on_after|fail_after)\(")
_HANDLES = re.compile(
    r"\b(try|except|catch|rescue)\b|\bErr\s*\(|\.is_err\(\)|\.is_ok\(\)|if let Ok|if let Err|"
    r"err\s*[!=]=\s*nil|\.catch\(|\.isFailure|\.onFailure|\bresult\.err\b|"
    r"\b(CURLE_OK|SQLITE_OK|EAGAIN|EINTR|errno)\b"  # C return-code checks
)
_SLEEPY = re.compile(r"(?i)(^|\.)(sleep|usleep|delay|sleep_for|sleep_until|backoff|wait_exponential)$")
_EXPO = re.compile(r"\*\*|\bpow\(|\.pow\(|<<|\*=\s*2|\bexponential|\bexpo\b|\bbackoff|checked_mul|saturating_mul|"
                   r"Math\.pow|math\.Pow|\*\s*2\b")
_COLLECTION = re.compile(
    r"^\s*(for|foreach)\s*\(?\s*(?:[\w&*<>\[\],\s]+?)\s*(\bin\b|\bof\b|:(?!:)|\bas\b)\s*(?!range\b|\d)"
    r"|:=\s*range\b|\bforeach\b"
)
_COUNT_PATTERNS = [
    # Python: for i in range(5) / range(1, 5)
    (re.compile(r"\bin\s+range\(\s*(?:([\w.]+)\s*,\s*)?([\w.]+)\s*[,)]"), "range"),
    # Rust: 0..5, 0..=5, 0..<5
    (re.compile(r"\bin\s+\(?\s*([\w.]+)\s*(\.\.=|\.\.<|\.\.|until|to)\s*([\w.]+)|<-\s*([\w.]+)\s*(until|to)\s*([\w.]+)"),
     "rangeop"),
    # C-like / Go / JS / Java: i = 0; i < 5;
    (re.compile(r"(?:=|:=)\s*(\d+)\s*;\s*\$?\w+\s*(<=?)\s*([\w.$]+)\s*;"), "cfor"),
]
_POLICY_BOUND = re.compile(
    r"(?i)\b\w*(retr(y|ies)|attempts?|tries)\w*\s*(>=?|<=?|==)\s*[\w.]+|"
    r"should_retry|RetryDecision|retry_policy|next_backoff|max_elapsed|max_retries|max_attempts|\.take\(\d+\)"
)
_WHILE_BOUND = re.compile(r"\bwhile\s*\(?\s*!?\s*[\w.]+\s*(<=?)\s*([\w.]+)")
_RETRY_DECOR = re.compile(
    r"@(?:[\w.]+\.)?(retry|retrying|on_exception|on_predicate|Retryable|Retry)\b\s*(\((?:[^()]|\([^()]*\))*\))?")


def _const(name: str, src: str) -> int | None:
    """Value of a simple integer constant defined in the same file (MAX_RETRIES = 5)."""
    if re.fullmatch(r"\d+", name):
        return int(name)
    last = name.split(".")[-1]
    m = re.search(rf"\b{re.escape(last)}\b\s*(?::\s*[\w<>\[\]]+\s*)?=\s*(\d+)\b", src)
    return int(m.group(1)) if m else None


def _count(header: str, src: str) -> tuple[str, int | None]:
    for rx, form in _COUNT_PATTERNS:
        m = rx.search(header)
        if not m:
            continue
        if form == "range":
            lo = _const(m.group(1), src) if m.group(1) else 0
            hi = _const(m.group(2), src)
            return "counted", (hi - lo if hi is not None and lo is not None else None)
        if form == "rangeop":
            lo_s, op, hi_s = (m.group(1), m.group(2), m.group(3)) if m.group(1) else (m.group(4), m.group(5), m.group(6))
            lo, hi = _const(lo_s, src), _const(hi_s, src)
            if lo is None or hi is None:
                return "counted", None
            return "counted", hi - lo + (1 if op in ("..=", "to") else 0)
        lo, op, hi = int(m.group(1)), m.group(2), _const(m.group(3), src)
        return "counted", (hi - lo + (1 if op == "<=" else 0) if hi is not None else None)
    return "", None


def _retry_decorator(text_: str) -> tuple[int | None, str] | None:
    m = _RETRY_DECOR.search(text_)
    if not m:
        return None
    kind, args = m.group(1), m.group(2) or ""
    a = re.search(r"(?:stop_after_attempt\(|max_tries\s*=\s*|stop_max_attempt_number\s*=\s*|maxAttempts\s*=\s*|"
                  r"\btries\s*=\s*|\battempts\s*=\s*)(\d+)", args)
    if a:
        attempts = int(a.group(1))
    elif kind in ("Retryable", "Retry"):
        attempts = 3  # Spring Retry / resilience4j default
    else:
        attempts = None  # tenacity, retrying and backoff retry forever without a stop condition
    if re.search(r"exponential|expo\b|multiplier", args):
        backoff = "exponential"
    elif re.search(r"wait_fixed|constant|delay|@Backoff", args) or kind in ("Retryable", "Retry"):
        backoff = "fixed"
    elif kind in ("on_exception", "on_predicate"):
        backoff = "exponential"  # backoff.on_exception always takes a wait generator
    else:
        backoff = "none"
    return attempts, backoff


def _loop_kind(n: Node) -> str:
    """"forever" for service tick loops (`loop {}`, `for {}`, `while True:`), else the node type."""
    if n.type == "loop_expression":
        return "forever"
    body = n.child_by_field_name("body")
    return "forever" if body is not None and _FOREVER.fullmatch(_header(n, body)) else n.type


def _statement(node: Node, body: Node) -> Node:
    n = node
    while n.parent is not None and n.parent.id != body.id and n.parent.type not in BLOCKISH:
        n = n.parent
    return n


def _fn_name(node: Node) -> str | None:
    n = node.child_by_field_name("name")
    if n is not None:
        return text(n)
    d = node.child_by_field_name("declarator")
    while d is not None:
        if d.type in NAME_TYPES:
            return text(d)
        d = d.child_by_field_name("declarator")
    for c in node.named_children:
        if c.type in ("simple_identifier", "identifier"):
            return text(c)
    return None


def _lambda_name(node: Node) -> str | None:
    p = node.parent
    if p is None:
        return None
    f = (p.child_by_field_name("name") or p.child_by_field_name("key")
         or p.child_by_field_name("left") or p.child_by_field_name("property"))
    if f is None or f.id == node.id:
        return None
    return normalize(text(f)).rsplit(".", 1)[-1] or None


def _container_name(node: Node) -> str | None:
    n = node.child_by_field_name("type") if node.type == "impl_item" else None
    n = n or node.child_by_field_name("name")
    if n is None:
        n = next((c for c in node.named_children if c.type in CONTAINER_NAME_TYPES), None)
    if n is None:
        return "Companion" if node.type == "companion_object" else None
    s = normalize(re.sub(r"<.*", "", text(n)).lstrip("&").strip())
    return s.rsplit(".", 1)[-1] or None


def _prefix(spec: LangSpec, node: Node) -> str:
    parts = []
    p = node.prev_named_sibling
    while p is not None and p.type in spec.attribute_types:
        parts.append(text(p))
        p = p.prev_named_sibling
    if node.parent is not None and node.parent.type == "decorated_definition":
        parts += [text(c) for c in node.parent.named_children if c.type == "decorator"]
    return " ".join(parts)


class FileExtractor:
    def __init__(self, spec: LangSpec, path: Path, root: Path, include_tests: bool = False):
        self.spec = spec
        self.src = path.read_bytes()
        self.rel = path.relative_to(root).as_posix()
        self.root_name = root.name
        self.include_tests = include_tests
        self.tree = parser(spec.grammar).parse(self.src)
        imports = spec.imports(self.tree.root_node)
        self.src_text = self.src.decode("utf8", "replace")
        m = spec.client_timeout.search(self.src_text) if spec.client_timeout else None
        self.fctx = FileCtx(self.rel, spec.name, imports, m is not None)
        if m is not None:  # the value, when it sits next to the configuration
            self.fctx.client_timeout_s = parse_duration(self.src_text[m.start():m.end() + 160], spec.name)
        else:
            self.fctx.untimed_client = bool(_UNTIMED_CLIENT.search(self.src_text))
        self.functions: list[Function] = []
        self.hook_hits: list[tuple[str, HookHit]] = []

    # structure -----------------------------------------------------------
    def _is_named_lambda(self, n: Node) -> bool:
        return (n.type in self.spec.lambda_types and n.parent is not None
                and n.parent.type in self.spec.named_lambda_parents and _lambda_name(n) is not None)

    def _is_function(self, n: Node) -> bool:
        return n.type in self.spec.function_types or self._is_named_lambda(n)

    def run(self) -> "FileExtractor":
        spec = self.spec
        rel = PurePath(self.rel)
        base = (spec.module_of(rel, self.root_name) if spec.module_of
                else default_module(rel, spec.file_is_module))
        stack = [(self.tree.root_node, base, None)]
        while stack:
            node, mods, cont = stack.pop()
            for child in node.named_children:
                t = child.type
                if t in spec.module_types:
                    name = _container_name(child)
                    if name in TEST_MODULES and not self.include_tests:
                        continue
                    stack.append((child, mods + ((name,) if name else ()), None))
                elif t in spec.container_types:
                    stack.append((child, mods, _container_name(child) or cont))
                elif self._is_function(child):
                    self._build(child, mods, cont)
                    stack.append((child, mods, cont))
                else:
                    stack.append((child, mods, cont))
        self.functions.sort(key=lambda f: (f.line, f.id))
        self.fctx.functions = [f.id for f in self.functions]
        return self

    def _build(self, node: Node, mods: tuple[str, ...], cont: str | None):
        spec = self.spec
        name = _lambda_name(node) if node.type in spec.lambda_types else _fn_name(node)
        if not name:
            return
        self_names: set[str] = set()
        if spec.receiver_info is not None:
            sname, tname = spec.receiver_info(node)
            cont = tname or cont
            if sname:
                self_names.add(sname)
        norm_name = normalize(name)
        if "." in norm_name:
            parts = norm_name.split(".")
            name, cont = parts[-1], parts[-2]
        body = _body(node)
        header = _header(node, body)
        is_async = bool(spec.async_header and spec.async_header.search(header))
        prefix = _prefix(spec, node)
        is_entry = (name == "main" and cont is None) or bool(
            spec.entry and spec.entry.search(prefix + " " + header))
        if spec.private_of is not None:
            private = spec.private_of(node, name, header)
        else:
            private = bool(spec.private_header and spec.private_header.search(prefix + " " + header))
        line, col = node.start_point[0] + 1, node.start_point[1]
        fn = Function(
            id=f"{self.rel}:{line}:{col}", lang=spec.name, name=name,
            qualname=spec.sep.join([*mods, *([cont] if cont else []), name]),
            file=self.rel, line=line, end_line=node.end_point[0] + 1, module=mods,
            container=cont, is_async=is_async, is_entry=is_entry, private=private,
            is_startup=bool(spec.startup and spec.startup.search(prefix + " " + header)),
        )
        if node.child_by_field_name("parameter") is not None:  # JS `x => ...`
            fn.arity = (0 if spec.arity_mode == "no_min" else 1, 1)
        else:
            fn.arity, fn.takes_self = _arity(spec, node, cont is not None)
        fn.retry = _retry_decorator(prefix + "\n" + header)
        scan = body if body is not None else node
        nesting = 0
        stack = [(scan, 0)]
        while stack:
            n, d = stack.pop()
            if n.type in spec.loop_types:
                d += 1
                nesting = max(nesting, d)
                fn.loops.append(self._loop_info(n))
            if n.type in spec.call_types:
                raw, name_node, _, scoped = callee(n)
                line = (name_node or n).start_point[0] + 1
                fn.calls.append(self._call(raw, scoped, line, n, _awaited(spec, n), fn, scan, self_names,
                                           _argc(n)))
            elif n.type in spec.macro_types:
                fn.calls += self._macro_calls(n, fn, scan, self_names)
            for c in n.named_children:
                if not self._is_function(c):
                    stack.append((c, d))
        fn.calls.sort(key=lambda c: (c.line, len(c.raw)))  # inner calls of a chain first
        fn.max_loop_depth = max([nesting, *(c.loop_depth for c in fn.calls)])
        for hook in spec.hooks:
            self.hook_hits += [(fn.id, h) for h in hook(spec, node, scan, is_async)]
        self.functions.append(fn)

    def _loop_info(self, n: Node) -> LoopInfo:
        body = n.child_by_field_name("body")
        if body is None:  # some grammars: the loop body is a plain child, not a field
            body = next((c for c in reversed(n.named_children) if c.type in LOOP_BODIES), None)
        header = _header(n, body) if body is not None else text(n).split("\n", 1)[0]
        body_text = text(body)[:8000] if body is not None else ""
        if _loop_kind(n) == "forever":
            kind, bound = "forever", None
        else:
            kind, bound = _count(header, self.src_text)
            if not kind:
                m = _WHILE_BOUND.search(header)
                if _COLLECTION.search(header):
                    kind = "collection"
                elif m:
                    lim = _const(m.group(2), self.src_text)
                    kind, bound = "conditional", (lim + (1 if m.group(1) == "<=" else 0) if lim is not None else None)
                elif re.match(r"\s*(while|do|loop|repeat|until)\b", header):
                    kind = "conditional"
                else:
                    kind = "collection"
        sleep_s = None
        if body is not None:
            for c in walk(body, self._is_function):
                if c.type in self.spec.call_types and _SLEEPY.search(normalize(callee(c)[0])):
                    sleep_s = max(sleep_s or 0.0, sleep_duration(text(c), self.spec.name) or 0.0)
        return LoopInfo(
            line=n.start_point[0] + 1, end_line=n.end_point[0] + 1, kind=kind, bound=bound,
            handles_errors=bool(_HANDLES.search(body_text)), sleep_s=sleep_s,
            exponential=sleep_s is not None and bool(_EXPO.search(body_text)),
            exits=bool(re.search(r"\b(break|return)\b", body_text)),
            retryish=bool(re.search(r"(?i)(attempt|retr(y|ies)|tries|backoff)", header + body_text)),
            policy_bound=bool(_POLICY_BOUND.search(body_text)),
        )

    # calls ---------------------------------------------------------------
    def _macro_calls(self, node: Node, fn: Function, body: Node, self_names: set[str]) -> list[Call]:
        """Calls inside an unparsed macro token tree, found by pattern with balanced parens."""
        macro = normalize(text(node.child_by_field_name("macro")))
        tt = next((c for c in node.named_children if c.type == "token_tree"), None)
        if tt is None:
            return []
        inner = text(tt)
        awaits_all = bool(self.spec.macro_awaits and self.spec.macro_awaits.search(macro))
        out = []
        for m in _MACRO_CALL.finditer(inner):
            raw = m.group(1)
            if raw.split("::")[-1].split(".")[-1].strip() in _NOT_CALLS:
                continue
            if m.start(1) > 0 and inner[m.start(1) - 1] == ".":
                raw = "_chain." + raw  # method on the result of an earlier call
            close = _matching_paren(inner, m.end() - 1)
            awaited = awaits_all or inner[close + 1:close + 8].lstrip().startswith(".await")
            line = tt.start_point[0] + 1 + inner.count("\n", 0, m.start(1))
            argc = len(split_top(inner[m.end():close]))
            out.append(self._call(raw, "::" in raw, line, node, awaited, fn, body, self_names, argc))
        return out

    def _call(self, raw: str, scoped: bool, line: int, call: Node, awaited: bool,
              fn: Function, body: Node, self_names: set[str], argc: int | None) -> Call:
        spec, imports = self.spec, self.fctx.imports
        norm = normalize(raw)
        recv, _, name = norm.rpartition(".")
        words = re.findall(r"[\w$!?]+", name)
        name = words[-1] if words else name
        root = recv.split(".")[0] if recv else ""
        selfish = spec.self_names | self_names
        if not recv:
            kind = "ident"
        elif root in selfish:
            kind = "method"
        elif scoped or "::" in raw or "\\" in raw or root in imports or (
                spec.capital_is_type and root[:1].isupper() and "." not in recv):
            kind = "path"
        else:
            kind = "method"

        has_timeout = False
        deadline_text = ""
        context = None
        loop_kinds: list[str] = []
        passed_lambda = False
        child, n = call, (call.parent if call.id != body.id else None)
        while n is not None:
            t = n.type
            if t in spec.loop_types:
                loop_kinds.append(_loop_kind(n))
            elif t == "with_statement":  # Python: async with asyncio.timeout(3):
                head = _header(n, n.child_by_field_name("body"))
                if _TIMEOUT_WITH.search(head):
                    has_timeout = True
                    deadline_text = deadline_text or head
            elif t in spec.async_scope_types:
                context = context or "async"
            elif t in spec.lambda_types:
                passed_lambda = True
                if context is None and spec.async_header is not None and spec.async_header.search(
                        _header(n, _body(n))):
                    context = "async"
            elif t in spec.call_types:
                o_raw, _, o_ids, _ = callee(n)
                if child.id not in o_ids:
                    o_path = canon(normalize(o_raw), imports)
                    probe = f"{o_path} {text(n)[:200]}"
                    if context is None and spec.offload is not None and spec.offload.search(probe):
                        context = "offloaded"
                    if context is None and spec.async_wrappers is not None and spec.async_wrappers.search(o_path):
                        context = "async"
                    if spec.timeout_call is not None and spec.timeout_call.search(o_path):
                        has_timeout = True
                        deadline_text = deadline_text or text(n)[:400]
                    if (not awaited and spec.await_combinators is not None
                            and spec.await_combinators.search(o_path) and _awaited(spec, n)):
                        awaited = True
                    if passed_lambda and o_path.rsplit(".", 1)[-1] in spec.iter_methods:
                        loop_kinds.append("iterator")
                passed_lambda = False
            if n.id == body.id:  # the body itself may be a call (expression-bodied functions)
                break
            child, n = n, n.parent

        stmt = _statement(call, body)
        stmt_text = text(stmt)
        tail = ""
        if call.type in spec.call_types and stmt.start_byte <= call.end_byte <= stmt.end_byte:
            tail = stmt.text[call.end_byte - stmt.start_byte:][:120].decode("utf8", "replace")
        if spec.timeout_text is not None and spec.timeout_text.search(stmt_text[:2000]):
            has_timeout = True
        timeout_s = None
        if deadline_text:
            timeout_s = parse_duration(deadline_text, spec.name)
        elif has_timeout:
            timeout_s = parse_duration(stmt_text[:2000], spec.name)
            if timeout_s is None and "ctx" in stmt_text:  # Go: deadline set on the context earlier
                m = re.search(r"With(Timeout|Deadline)\([^\n]*", text(body)[: max(0, call.start_byte - body.start_byte)])
                timeout_s = parse_duration(m.group(0), spec.name) if m else None
        return Call(
            name=name, raw=re.sub(r"\s+", " ", raw)[:120],
            path=canon(norm, imports) if norm else name,
            receiver=recv, kind=kind,
            line=line,
            awaited=awaited,
            context=context or ("async" if fn.is_async else "sync"),
            loop_depth=len(loop_kinds), loop_kinds=loop_kinds,
            has_timeout=has_timeout,
            snippet=" ".join(stmt_text.split())[:140],
            self_call=kind == "method" and recv in selfish,
            argc=argc,
            stmt_line=stmt.start_point[0] + 1,
            timeout_s=timeout_s,
            tail=tail,
        )
