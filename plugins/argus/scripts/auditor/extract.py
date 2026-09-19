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
from .model import Call, FileCtx, Function, HookHit

BLOCKISH = frozenset({
    "block", "statement_block", "compound_statement", "statements", "body_statement",
    "declaration_list", "function_body", "statement_list", "class_body", "source_file",
    "program", "module", "do_block", "template_body", "field_declaration_list",
    "control_structure_body", "constructor_body", "switch_block", "match_block",
})
BODY_TYPES = ("function_body", "block", "compound_statement", "statement_block", "body_statement")
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
    if len(parts) > 2 and parts[0] in ("main", "test") and parts[1] in ("java", "kotlin", "scala"):
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
    elif spec.name == "swift":
        segs = [text(c) for c in node.named_children if c.type == "parameter"]
    else:
        return None, False
    if segs == ["void"]:
        segs = []
    takes_self = False
    if segs and ((_SELF_PARAM.match(segs[0]) and (spec.name == "rust" or (spec.name == "python" and in_container)))
                 or (spec.name == "csharp" and segs[0].startswith("this "))):  # C# extension methods
        segs, takes_self = segs[1:], True
    segs = [s for s in segs if s not in ("*", "/") and not s.startswith("&")]  # py separators, ruby &block
    variadic = any(_VARIADIC.search(s) for s in segs)

    def optional(s):
        return bool(_DEFAULT.search(s)) or (spec.name == "ruby" and re.match(r"^\w+:", s) is not None)

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
    if args.type == "call_suffix":  # Kotlin/Swift: value arguments plus trailing lambda
        n = 0
        for c in args.named_children:
            if c.type == "value_arguments":
                n += sum(1 for a in c.named_children if a.type == "value_argument")
            elif c.type in ("annotated_lambda", "lambda_literal"):
                n += 1
        return n
    if args.type not in ARG_CONTAINERS:
        return 1  # `f(x for x in y)`, Scala `Future { ... }`
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
        client_timeout = bool(spec.client_timeout and spec.client_timeout.search(
            self.src.decode("utf8", "replace")))
        self.fctx = FileCtx(self.rel, spec.name, imports, client_timeout)
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
        )
        if node.child_by_field_name("parameter") is not None:  # JS `x => ...`
            fn.arity = (0 if spec.arity_mode == "no_min" else 1, 1)
        else:
            fn.arity, fn.takes_self = _arity(spec, node, cont is not None)
        scan = body if body is not None else node
        nesting = 0
        stack = [(scan, 0)]
        while stack:
            n, d = stack.pop()
            if n.type in spec.loop_types:
                d += 1
                nesting = max(nesting, d)
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
        context = None
        loop_kinds: list[str] = []
        passed_lambda = False
        child, n = call, (call.parent if call.id != body.id else None)
        while n is not None:
            t = n.type
            if t in spec.loop_types:
                loop_kinds.append(_loop_kind(n))
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
        if spec.timeout_text is not None and spec.timeout_text.search(stmt_text[:2000]):
            has_timeout = True
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
        )
