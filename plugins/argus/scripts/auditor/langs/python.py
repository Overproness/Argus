import re

from ..model import HookHit
from .base import CPU, DB, FS, NET, PROCESS, RUNTIME, SLEEP, WAIT, LangSpec, R, text, walk
from .common import API_NAME, PY_CTOR

HTTP_VERBS = {"get", "post", "put", "patch", "delete", "head", "options", "request", "send", "stream"}


def imports(root):
    out = {}
    for n in walk(root):
        if n.type == "import_statement":
            for c in n.children_by_field_name("name"):
                if c.type == "aliased_import":
                    out[text(c.child_by_field_name("alias"))] = text(c.child_by_field_name("name"))
                else:
                    full = text(c)
                    out[full.split(".")[0]] = full.split(".")[0]
                    out["\0" + full] = full
        elif n.type == "import_from_statement":
            mod = text(n.child_by_field_name("module_name")).lstrip(".")
            for c in n.children_by_field_name("name"):
                if c.type == "aliased_import":
                    name = text(c.child_by_field_name("name"))
                    out[text(c.child_by_field_name("alias"))] = f"{mod}.{name}" if mod else name
                else:
                    name = text(c)
                    out[name.split(".")[-1]] = f"{mod}.{name}" if mod else name
    return out


def grpc_stream_reads(fn_node, body, imported):
    """Expose implicit network reads from generated grpc.aio streaming stubs.

    An arbitrary async iterator or a method called Subscribe is not enough:
    require a generated stub constructed with a known aio channel. Deadlines
    on the subscription or around the read are respected by normal analysis.
    """
    def path(node):
        s = text(node)
        first, dot, rest = s.partition(".")
        return imported.get(first, first) + (dot + rest if dot else "")

    channels, stubs, streams = set(), set(), {}
    hits = []
    for n in walk(body, _skip_nested):
        if n.type == "with_item":
            value = n.child_by_field_name("value")
            if value is not None and value.type == "as_pattern":
                call = value.named_children[0]
                alias = value.child_by_field_name("alias")
                if call.type == "call" and path(call.child_by_field_name("function")) in {
                        "grpc.aio.secure_channel", "grpc.aio.insecure_channel"}:
                    channels.add(text(alias))
        elif n.type == "assignment":
            left, right = n.child_by_field_name("left"), n.child_by_field_name("right")
            name = text(left)
            channels.discard(name)
            stubs.discard(name)
            streams.pop(name, None)
            if right is None or right.type != "call":
                continue
            func = right.child_by_field_name("function")
            args = right.child_by_field_name("arguments")
            p = path(func)
            if p in {"grpc.aio.secure_channel", "grpc.aio.insecure_channel"}:
                channels.add(name)
            elif re.search(r"(?:^|\.)\w+_pb2_grpc\.\w+Stub$", p) and args is not None and any(
                    text(a) in channels for a in args.named_children):
                stubs.add(name)
            elif func is not None and func.type == "attribute" and text(func.child_by_field_name("object")) in stubs:
                streams[name] = bool(args is not None and any(
                    a.type == "keyword_argument" and text(a.child_by_field_name("name")) == "timeout"
                    and text(a.child_by_field_name("value")) not in {"None", ""}
                    for a in args.named_children))
            elif func is not None and func.type == "attribute" and text(func.child_by_field_name("attribute")) == "__aiter__":
                receiver = text(func.child_by_field_name("object"))
                if receiver in streams:
                    streams[name] = streams[receiver]
        elif n.type == "for_statement" and text(n).lstrip().startswith("async for "):
            right = n.child_by_field_name("right")
            if text(right) in streams:
                hits.append(("grpc.aio.stream_read", right, streams[text(right)]))
        elif n.type == "call":
            func = n.child_by_field_name("function")
            if func is not None and func.type == "attribute" and text(func.child_by_field_name("attribute")) in {"__anext__", "read"}:
                receiver = text(func.child_by_field_name("object"))
                if receiver in streams:
                    hits.append(("grpc.aio.stream_read", n, streams[receiver]))
    return hits


LOCKISH = re.compile(r"(?i)(^|[._])(r?lock|mutex|sem(aphore)?|cond(ition)?)\w*$|lock\b")
MUTATORS = frozenset({"append", "extend", "insert", "remove", "pop", "popitem", "clear", "update", "setdefault",
                      "add", "discard", "appendleft", "popleft", "sort", "reverse"})
_CONTAINER_CALL = re.compile(r"^(dict|list|set|defaultdict|OrderedDict|deque|Counter|collections\.\w+)\s*\(")
_NESTED = ("function_definition", "lambda", "class_definition")


def _skip_nested(n) -> bool:
    return n.type in _NESTED


def _root(node):
    while node.parent is not None:
        node = node.parent
    return node


def _module_state(root) -> set[str]:
    """Module-level names bound to mutable containers: state every request and task shares."""
    out = set()
    for st in root.named_children:
        node = st.named_children[0] if st.type == "expression_statement" and st.named_children else st
        if node.type != "assignment":
            continue
        left, right = node.child_by_field_name("left"), node.child_by_field_name("right")
        if left is None or right is None or left.type != "identifier":
            continue
        if right.type in ("dictionary", "list", "set", "dictionary_comprehension", "list_comprehension",
                          "set_comprehension") or (right.type == "call" and _CONTAINER_CALL.match(text(right))):
            out.add(text(left))
    return out


def _globals_declared(body) -> set[str]:
    out = set()
    for n in walk(body, _skip_nested):
        if n.type == "global_statement":
            out.update(text(c) for c in n.named_children if c.type == "identifier")
    return out


def _locals(fn_node, body) -> set[str]:
    """Parameters and names assigned in this function: they shadow module state of the same name."""
    out = set()
    params = fn_node.child_by_field_name("parameters")
    for n in walk(params) if params is not None else []:
        if n.type == "identifier" and n.parent is not None and n.parent.type != "attribute":
            out.add(text(n))
    for n in walk(body, _skip_nested):
        if n.type in ("assignment", "augmented_assignment", "for_statement"):
            left = n.child_by_field_name("left")
            for t in [left] + (list(left.named_children) if left is not None and left.type in (
                    "pattern_list", "tuple_pattern") else []):
                if t is not None and t.type == "identifier":
                    out.add(text(t))
    return out


def _base_name(n):
    """The identifier at the root of `x`, `x[k]`, `x.attr`, `x[k].attr` (None for anything else)."""
    while n is not None and n.type in ("subscript", "attribute"):
        n = n.child_by_field_name("value") if n.type == "subscript" else n.child_by_field_name("object")
    return text(n) if n is not None and n.type == "identifier" else None


def _state_events(body, names: set[str]) -> list[tuple[int, int, str, str]]:
    """(byte, line, 'read'|'write', name) for every use of a shared name, in source order."""
    writes: dict[int, str] = {}  # identifier node id -> name, for identifiers that are written
    for n in walk(body, _skip_nested):
        target = None
        if n.type in ("assignment", "augmented_assignment"):
            target = n.child_by_field_name("left")
        elif n.type == "delete_statement":
            target = n.named_children[0] if n.named_children else None
        elif n.type == "call":
            f = n.child_by_field_name("function")
            if f is not None and f.type == "attribute" and text(f.child_by_field_name("attribute")) in MUTATORS:
                target = f.child_by_field_name("object")
        if target is None:
            continue
        for t in ([target] if target.type != "pattern_list" else target.named_children):
            nm = _base_name(t)
            if nm in names:
                ident = t
                while ident.type != "identifier":
                    ident = ident.child_by_field_name("value") or ident.child_by_field_name("object")
                writes[ident.id] = nm
    out = []
    for n in walk(body, _skip_nested):
        if n.type == "identifier" and text(n) in names:
            p = n.parent
            if p is not None and p.type == "global_statement":
                continue
            if p is not None and p.type == "keyword_argument" and p.child_by_field_name("name") == n:
                continue
            kind = "write" if n.id in writes else "read"
            out.append((n.start_byte, n.start_point[0] + 1, kind, text(n)))
            if kind == "write" and p is not None and p.type == "augmented_assignment":
                out.append((n.start_byte, n.start_point[0] + 1, "read", text(n)))
    out.sort()
    return out


def _lock_blocks(body) -> list[tuple[int, int]]:
    """Byte ranges held under `async with <lock>` / `with <lock>`: code inside is serialized."""
    out = []
    for n in walk(body, _skip_nested):
        if n.type == "with_statement":
            items = " ".join(text(c) for c in n.named_children if c.type == "with_clause")
            if re.search(r"(?i)lock|mutex|semaphore", items):
                out.append((n.start_byte, n.end_byte))
    return out


def race_across_await(spec, fn_node, body, is_async):
    """Shared state read before an `await` and written after it: other coroutines run in between."""
    if not is_async:
        return []
    declared = _globals_declared(body)
    names = (_module_state(_root(fn_node)) - _locals(fn_node, body)) | declared
    if not names:
        return []
    awaits = sorted(n.end_byte for n in walk(body, _skip_nested) if n.type == "await")
    if not awaits:
        return []
    guarded = _lock_blocks(body)

    def same_guard(a, b):
        return any(lo <= a < hi and lo <= b < hi for lo, hi in guarded)

    hits, seen = [], set()
    events = _state_events(body, names)
    for i, (rb, rl, kind, name) in enumerate(events):
        if kind != "read" or name in seen:
            continue
        for wb, wl, kind2, name2 in events[i + 1:]:
            if kind2 != "write" or name2 != name or same_guard(rb, wb):
                continue
            between = [a for a in awaits if rb < a <= wb]
            if not between:
                continue
            seen.add(name)
            hits.append(HookHit(
                "race-across-await", "high", rl,
                f"Shared `{name}` is read here and written at line {wl}, with an `await` in between. Other requests "
                "and tasks run during the await and change it, so this write loses their update or acts on a stale "
                "check (lost update, check-then-act, double spend). Hold one asyncio.Lock across the read and the "
                "write, or remove the await between them.",
            ))
            break
    return hits


def mutate_while_iterating(spec, fn_node, body, is_async):
    """`for x in items:` whose body adds to or removes from `items`."""
    hits = []
    for n in walk(body, _skip_nested):
        if n.type != "for_statement":
            continue
        right = n.child_by_field_name("right")
        if right is None:
            continue
        it = right
        if it.type == "call":  # d.items() / d.keys() / d.values()
            f = it.child_by_field_name("function")
            if f is None or f.type != "attribute" or text(f.child_by_field_name("attribute")) not in (
                    "items", "keys", "values"):
                continue
            it = f.child_by_field_name("object")
        if it is None or it.type not in ("identifier", "attribute"):
            continue
        coll = text(it)
        loop_body = n.child_by_field_name("body")
        for c in walk(loop_body, _skip_nested) if loop_body is not None else []:
            mutated = False
            if c.type == "call":
                f = c.child_by_field_name("function")
                mutated = (f is not None and f.type == "attribute" and text(f.child_by_field_name("object")) == coll
                           and text(f.child_by_field_name("attribute")) in MUTATORS - {"sort", "reverse"})
            elif c.type == "delete_statement":
                mutated = any(t.type == "subscript" and text(t.child_by_field_name("value")) == coll
                              for t in walk(c))
            if mutated:
                hits.append(HookHit(
                    "mutate-while-iterating", "medium", c.start_point[0] + 1,
                    f"`{coll}` is changed while a for loop iterates over it. A list silently skips the element "
                    "after every removal; a dict or set raises RuntimeError. If another thread also touches it, "
                    "results depend on timing. Iterate over a copy (`list(...)`) or build a new collection.",
                ))
                return hits
    return hits


_LOCK_CTOR = re.compile(r"^(threading\.|asyncio\.|multiprocessing\.)?(R?Lock|Semaphore|BoundedSemaphore|Condition)\s*\(")


def _module_locks(root) -> set[str]:
    """Module-level names bound to a lock constructor, whatever they are called."""
    out = set()
    for st in root.named_children:
        node = st.named_children[0] if st.type == "expression_statement" and st.named_children else st
        if node.type == "assignment":
            left, right = node.child_by_field_name("left"), node.child_by_field_name("right")
            if left is not None and right is not None and left.type == "identifier" and _LOCK_CTOR.match(text(right)):
                out.add(text(left))
    return out


def _is_lock(t: str, known: set[str]) -> bool:
    return t in known or bool(LOCKISH.search(t))


def _lock_regions(body, known: set[str]) -> list[tuple[str, int, int, str]]:
    """(lock text, first line, last line, how) for every lock this function takes."""
    out = []
    acquires = []
    for n in walk(body, _skip_nested):
        if n.type == "with_statement":
            for c in walk(n):
                if c.type == "with_item":
                    v = c.child_by_field_name("value") or (c.named_children[0] if c.named_children else None)
                    t = text(v)
                    if v is not None and v.type in ("identifier", "attribute") and _is_lock(t, known):
                        out.append((t, n.start_point[0] + 1, n.end_point[0] + 1,
                                    "async with" if text(n).startswith("async") else "with"))
                if c.type == "block":
                    break
        elif n.type == "call":
            f = n.child_by_field_name("function")
            if f is not None and f.type == "attribute" and text(f.child_by_field_name("attribute")) in (
                    "acquire", "release"):
                obj = text(f.child_by_field_name("object"))
                if _is_lock(obj, known):
                    acquires.append((n.start_byte, n.start_point[0] + 1, text(f.child_by_field_name("attribute")),
                                     obj, n))
    acquires.sort(key=lambda a: a[0])
    end_line = body.end_point[0] + 1
    for i, (_, line, what, obj, _n) in enumerate(acquires):
        if what != "acquire":
            continue
        rel = next((l for _, l, w, o, _ in acquires[i + 1:] if w == "release" and o == obj), end_line)
        out.append((obj, line, rel, "acquire"))
    return out


def _in_finally(n) -> bool:
    p = n.parent
    while p is not None and p.type not in _NESTED:
        if p.type == "finally_clause":
            return True
        p = p.parent
    return False


def locks(spec, fn_node, body, is_async):
    """Lock discipline: held across await, acquired without a guaranteed release, and (via `_lock-region`
    hits consumed by the analysis) the order in which locks nest, for lock-order inversions across functions."""
    hits = []
    known = _module_locks(_root(fn_node))
    for n in walk(body, _skip_nested):
        if n.type != "with_statement" or text(n).startswith("async") or not is_async:
            continue
        items = " ".join(text(c) for c in n.named_children if c.type == "with_clause")
        if not (re.search(r"(?i)lock|mutex|semaphore", items) or any(
                re.search(rf"\b{re.escape(k)}\b", items) for k in known)):
            continue
        body_node = n.child_by_field_name("body")
        if body_node is not None and any(d.type == "await" for d in walk(body_node)):
            hits.append(HookHit(
                "lock-across-await", "high", n.start_point[0] + 1,
                f"Sync lock (`with {items[:60]}`) held across `await`. Other threads block for the whole "
                "await, and another coroutine taking the same lock blocks the event loop thread itself: the "
                "whole server deadlocks. Use asyncio.Lock.",
            ))
    calls = []
    for n in walk(body, _skip_nested):
        if n.type == "call":
            f = n.child_by_field_name("function")
            if f is not None and f.type == "attribute" and text(f.child_by_field_name("attribute")) in (
                    "acquire", "release"):
                obj = text(f.child_by_field_name("object"))
                if _is_lock(obj, known):
                    awaited = n.parent is not None and n.parent.type == "await"
                    calls.append((n.start_byte, text(f.child_by_field_name("attribute")), obj, n, awaited))
    calls.sort(key=lambda c: c[0])
    awaits = sorted(n.start_byte for n in walk(body, _skip_nested) if n.type == "await")
    for i, (pos, what, obj, node, awaited) in enumerate(calls):
        if what != "acquire":
            continue
        releases = [c for c in calls[i + 1:] if c[1] == "release" and c[2] == obj]
        if not any(_in_finally(c[3]) for c in releases):
            hits.append(HookHit(
                "lock-not-released", "high", node.start_point[0] + 1,
                f"`{obj}.acquire()` without a `release()` in a `finally`: an exception or early return in between "
                "leaves the lock held forever, and every later caller blocks on it. Use `with` / `async with`.",
            ))
        if is_async and not awaited:
            end = releases[0][0] if releases else body.end_byte
            if any(pos < a < end for a in awaits):
                hits.append(HookHit(
                    "lock-across-await", "high", node.start_point[0] + 1,
                    f"Thread lock `{obj}` acquired in a coroutine and held across `await`. The next request's "
                    f"`{obj}.acquire()` runs on the event loop thread and blocks it until the first releases, but "
                    "the first can only release on that same thread: the whole server deadlocks. Use asyncio.Lock.",
                ))
    for lock, lo, hi, how in _lock_regions(body, known):
        cls = fn_node.parent
        while cls is not None and cls.type != "class_definition":
            cls = cls.parent
        owner = text(cls.child_by_field_name("name")) if cls is not None and lock.startswith("self.") else ""
        hits.append(HookHit("_lock-region", "info", lo, f"{owner}|{lock}|{lo}|{hi}|{how}"))
    return hits


_WRITE_SQL = re.compile(r"""^[rbuf]*(["']{1,3})\s*(INSERT|UPDATE|DELETE|REPLACE|UPSERT)\b""", re.I)


def uncommitted_write(spec, fn_node, body, is_async):
    """An INSERT/UPDATE/DELETE with no commit on the connection: the transaction stays open."""
    root_src = text(_root(fn_node))
    if re.search(r"isolation_level\s*=\s*None|autocommit\s*=\s*True|\.autocommit\s*=\s*True", root_src):
        return []
    for n in walk(body, _skip_nested):
        if n.type != "call":
            continue
        f = n.child_by_field_name("function")
        args = n.child_by_field_name("arguments")
        if (f is None or f.type != "attribute" or text(f.child_by_field_name("attribute")) not in (
                "execute", "executemany") or args is None or not args.named_children):
            continue
        if not _WRITE_SQL.match(text(args.named_children[0])):
            continue
        conn = text(f.child_by_field_name("object"))
        src = text(body)
        if re.search(r"\.commit\(|\bwith\s+" + re.escape(conn) + r"\b|\bbegin\(", src):
            return []
        return [HookHit(
            "uncommitted-write", "medium", n.start_point[0] + 1,
            f"Writes through `{conn}` and never commits. The change sits in an open transaction: other "
            "connections do not see it, it is lost on restart, and (SQLite) it holds the write lock so other "
            "writers fail with 'database is locked'. Commit, or use the connection as a context manager.",
        )]
    return []


SHRINK_OPS = frozenset({"pop", "popitem", "remove", "discard", "popleft", "clear"})
GROW_OPS = frozenset({"append", "extend", "insert", "setdefault", "add", "appendleft", "update"})
_CACHE_NAMEISH = re.compile(r"(?i)cache|_jobs?$|^jobs?$|registry|sessions?$|seen|index|table|store|pool|map$")


def unbounded_module_container(spec, fn_node, body, is_async):
    """A module-level dict/list/set that is only ever grown (append/add/[k]=v) in the whole file, never
    popped, removed or cleared anywhere: it grows for the life of the process."""
    root = _root(fn_node)
    names = _module_state(root)
    if not names:
        return []
    grows: dict[str, int] = {}
    shrinks: set[str] = set()
    for n in walk(root):  # the whole file: growth and eviction can be in different functions
        if n.type == "call":
            f = n.child_by_field_name("function")
            if f is not None and f.type == "attribute":
                obj, method = text(f.child_by_field_name("object")), text(f.child_by_field_name("attribute"))
                if obj in names:
                    if method in SHRINK_OPS:
                        shrinks.add(obj)
                    elif method in GROW_OPS and obj not in grows:
                        grows[obj] = n.start_point[0] + 1
        elif n.type == "assignment":
            left = n.child_by_field_name("left")
            if left is not None and left.type == "subscript":
                base = _base_name(left)
                if base in names and base not in grows:
                    grows[base] = left.start_point[0] + 1
        elif n.type == "delete_statement":
            for t in walk(n):
                if t.type == "subscript":
                    base = _base_name(t)
                    if base in names:
                        shrinks.add(base)
    fn_lo, fn_hi = fn_node.start_point[0] + 1, fn_node.end_point[0] + 1
    hits = []
    for name, line in grows.items():
        if name in shrinks or not (fn_lo <= line <= fn_hi):
            continue  # bounded elsewhere, or this function isn't the one that grows it (avoid duplicates)
        hint = " It is never read back by key either; check it is still needed at all." if not _CACHE_NAMEISH.search(
            name) else ""
        hits.append(HookHit(
            "unbounded-module-container", "medium", line,
            f"Module-level `{name}` is only ever added to across this file, never popped, removed or cleared. "
            "Every request or background cycle that adds to it grows the process's memory a little more, "
            f"for as long as the process runs.{hint} Evict old entries (an LRU/TTL cache, a max size, or a "
            "cleanup pass) or move it out of module scope.",
        ))
    return hits


_CLIENT_CTOR = re.compile(r"^(httpx\.(Async)?Client|aiohttp\.ClientSession|requests\.Session)\s*\(")
_FOREVER_WHILE = re.compile(r"(?i)while\s+(true|1)\s*:")


def _with_targets(n) -> list[str]:
    """Variable names a `with`/`async with` statement binds (`as x`) or enters bare (the ctx-manager expr)."""
    out = []
    for c in walk(n, lambda c: c.type in _NESTED):
        if c.type != "with_item":
            continue
        item = c.named_children[0] if c.named_children else None
        if item is not None and item.type == "as_pattern":
            value, target = item.named_children[0], item.named_children[-1]
            if value.type in ("identifier", "attribute"):
                out.append(text(value))
            out.append(text(target))  # as_pattern_target, or an identifier in grammars without it
        elif item is not None and item.type in ("identifier", "attribute"):
            out.append(text(item))
    return out


def client_per_call(spec, fn_node, body, is_async):
    """An HTTP client/session constructed inside a function body (not reused across a long-lived loop):
    every call to this function opens its own connection pool instead of reusing one."""
    for n in walk(body, _skip_nested):
        if not (n.type == "call" and _CLIENT_CTOR.match(text(n.child_by_field_name("function")) + "(")):
            continue
        ctor = n
        # The constructed client, by name: `httpx.AsyncClient()` entered directly, or assigned then entered
        # later (`client = httpx.AsyncClient()` ... `async with client as c:`).
        names = set()
        assign = ctor.parent
        rhs = assign.child_by_field_name("right") if assign is not None and assign.type == "assignment" else None
        if rhs is not None and rhs.id == ctor.id:
            left = assign.child_by_field_name("left")
            if left is not None and left.type == "identifier":
                names.add(text(left))
        long_lived = False
        for w in walk(body, _skip_nested):
            if w.type != "with_statement":
                continue
            targets = _with_targets(w)
            entered_here = ctor.start_byte >= w.start_byte and ctor.end_byte <= w.end_byte
            entered_by_name = bool(names & set(targets))
            if not (entered_here or entered_by_name):
                continue
            w_body = w.child_by_field_name("body")
            if w_body is not None and _FOREVER_WHILE.search(text(w_body)[:400]):
                long_lived = True  # built once, then reused inside a `while True:` loop: long-lived, fine
                break
        if long_lived:
            continue
        return [HookHit(
            "client-per-call", "medium", ctor.start_point[0] + 1,
            "An HTTP client/session is constructed inside this function. If this function runs per request "
            "(or per loop iteration), it opens a brand-new connection pool every time instead of reusing one: "
            "slower (a new TCP/TLS handshake each call), and under load it can exhaust sockets. Build the "
            "client once (module scope, or app startup) and reuse it.",
        )]
    return []


COMMENT = re.compile(r"^\s*#")
_HTTP_RESP_ASSIGN = re.compile(
    r"^\s*(\w+)\s*=\s*(?:await\s+)?[\w.]*\.(get|post|put|patch|delete|request)\s*\(")
_STATUS_CHECKED = re.compile(r"raise_for_status\s*\(|\.status_code\s*(==|!=|>=|<=|<|>)|\.ok\b|\.is_success\b")
_RESP_CONSUMED = re.compile(r"\.(json|text|content)\b")


def unchecked_response(spec, fn_node, body, is_async):
    """An HTTP response is parsed or stored without checking its status first: a 4xx/5xx body is treated
    as if it were a success. Scanned line by line (not node-typed): a status check can be an `if`, an
    `assert`, or a bare call, and all of those are different node shapes in the grammar."""
    pending: dict[str, bool] = {}  # var -> checked
    base = body.start_point[0] + 1
    for i, line in enumerate(text(body).split("\n")):
        if COMMENT.match(line):
            continue
        ln = base + i
        m = _HTTP_RESP_ASSIGN.match(line)
        if m:
            pending[m.group(1)] = False
            continue
        for var in list(pending):
            if not re.search(rf"\b{re.escape(var)}\b", line):
                continue
            if _STATUS_CHECKED.search(line):
                pending[var] = True
            elif re.search(rf"\b{re.escape(var)}\b" + r"\s*" + _RESP_CONSUMED.pattern, line) and not pending[var]:
                return [HookHit(
                    "unchecked-response", "medium", ln,
                    f"`{var}` is read here with no status check (`raise_for_status()` / `.status_code` / "
                    "`.ok`) between the request and this use. An error response (4xx/5xx, or a non-JSON body) "
                    "is parsed or cached as if it were a success, and the failure surfaces later, far from "
                    "its cause. Check the status before using the body.",
                )]
    return []


def threads_and_fanout(spec, fn_node, body, is_async):
    """A thread started per call and never joined; tasks started per item with no cap."""
    hits = []
    src = text(body)
    for n in walk(body, _skip_nested):
        if n.type != "call":
            continue
        f = text(n.child_by_field_name("function"))
        if re.search(r"(^|\.)Thread$", f) and not re.search(r"\w\.join\(\s*(timeout\s*=\s*)?[\d.]*\s*\)", src):
            hits.append(HookHit(
                "unbounded-threads", "medium", n.start_point[0] + 1,
                "Starts a new OS thread on every call and never joins it. Under load threads pile up without a "
                "cap (each holds a stack and whatever it waits on) until the process runs out of memory or "
                "threads. Use a bounded ThreadPoolExecutor or a queue with fixed workers.",
            ))
            break
    if is_async and not re.search(r"(?i)semaphore|bounded|limiter|max_concurrency|chunks?\(|batch", src):
        for n in walk(body, _skip_nested):
            if n.type != "call":
                continue
            f = text(n.child_by_field_name("function"))
            args = n.child_by_field_name("arguments")
            if re.search(r"(^|\.)gather$", f) and args is not None and any(
                    c.type == "list_splat" for c in args.named_children):
                hits.append(HookHit(
                    "unbounded-concurrency", "medium", n.start_point[0] + 1,
                    "`gather(*...)` starts one task per item at once with no cap. A large input means thousands of "
                    "concurrent tasks, sockets or DB connections and the memory for all of them, and one failure "
                    "does not stop the rest. Bound it with asyncio.Semaphore (or a worker pool) and add a timeout.",
                ))
                break
    return hits


SPEC = LangSpec(
    name="python",
    grammar="python",
    extensions=(".py",),
    function_types=frozenset({"function_definition"}),
    call_types=frozenset({"call"}),
    loop_types=frozenset({
        "for_statement", "while_statement", "list_comprehension", "set_comprehension",
        "dictionary_comprehension", "generator_expression",
    }),
    container_types=frozenset({"class_definition"}),
    lambda_types=frozenset({"lambda"}),
    await_types=frozenset({"await"}),
    has_async=True,
    async_header=re.compile(r"^\s*async\s+def\b"),
    self_names=frozenset({"self", "cls"}),
    iter_methods=frozenset({"map", "filter", "sorted", "min", "max", "apply", "applymap", "transform", "agg"}),
    offload=re.compile(r"(^|\.)(to_thread|run_in_executor|run_sync|submit)\s"),
    await_combinators=re.compile(r"(^|\.)(gather|wait_for|wait|shield|timeout|create_task|ensure_future)$"),
    timeout_call=re.compile(r"(^|\.)(wait_for|timeout|timeout_at|move_on_after|fail_after)$"),
    timeout_text=re.compile(r"\btimeout\s*=|ClientTimeout\(|async with (asyncio\.)?timeout"),
    client_timeout=re.compile(r"(Session|Client|AsyncClient)\([^)]*timeout\s*=|ClientTimeout\("),
    entry=re.compile(
        r"@\w*(app|router|bp|blueprint|api)\.(get|post|put|patch|delete|route|websocket)\b"
        r"|@(\w+\.)?(task|shared_task)\b"
    ),
    rules=[
        R("rpc", NET, path=r"^grpc\.aio\.stream_read$", awaited=True, client_level=False),
        R("http", NET, path=r"^requests\.", blocking=True, exclude_names=PY_CTOR, default_client=True),
        R("http", NET, methods=HTTP_VERBS, receiver=r"(?i)session|client|http|requests", imp=r"^requests\b",
          blocking=True, client_level=False),
        R("http", NET, path=r"^urllib\.request\.(urlopen|urlretrieve)$", blocking=True),
        R("http", NET, path=r"^urllib3\.", blocking=True, exclude_names=PY_CTOR),
        R("http", NET, path=r"^httpx\.(get|post|put|patch|delete|head|request|stream)$", blocking=True,
          default_timeout="5s (httpx default)"),
        R("http", NET, methods=HTTP_VERBS, receiver=r"(?i)client|session|http", imp=r"^httpx\b", awaited=True,
          default_timeout="5s (httpx default)"),
        R("http", NET, methods=HTTP_VERBS, receiver=r"(?i)client|session|http", imp=r"^httpx\b", blocking=True,
          default_timeout="5s (httpx default)"),
        R("http", NET, methods=HTTP_VERBS - {"send"}, receiver=r"(?i)session|client|http", imp=r"^aiohttp\b",
          awaited=True, default_timeout="300s total (aiohttp ClientTimeout default)"),
        R("sleep", SLEEP, path=r"^time\.sleep$", blocking=True),
        R("process", PROCESS, path=r"^(subprocess\.(run|call|check_call|check_output)|os\.system)$", blocking=True),
        R("net", NET, path=r"^socket\.(create_connection|getaddrinfo|gethostbyname)$", blocking=True),
        R("db", DB, path=r"^sqlite3\.", blocking=True, exclude_names=PY_CTOR,
          default_timeout="5s (sqlite3 busy timeout default)"),
        R("db", DB, methods={"execute", "executemany", "executescript", "fetchall", "fetchone", "commit"},
          imp=r"^sqlite3\b", blocking=True, default_timeout="5s (sqlite3 busy timeout default)"),
        R("db", DB, path=r"^(psycopg2?|pymysql|MySQLdb|pymongo|redis)\.", blocking=True, exclude_names=PY_CTOR),
        R("db", DB, methods={"execute", "executemany", "fetchall", "fetchone", "commit"},
          imp=r"^(psycopg2?|pymysql|MySQLdb|sqlalchemy|django\.db)\b", blocking=True),
        R("db", DB, methods={"get", "first", "count", "exists", "create", "save", "delete", "update", "aggregate", "one", "scalar"},
          receiver=r"\.objects\b|\.query\b|(?i:session)", imp=r"^(django|sqlalchemy|flask_sqlalchemy)\b", blocking=True),
        R("db", DB, methods={"execute", "fetch", "fetchrow", "fetchval", "find_one", "insert_one", "get", "set"},
          receiver=r"(?i)conn|pool|db|redis|collection|session",
          imp=r"^(asyncpg|motor|databases|sqlalchemy\.ext\.asyncio|redis\.asyncio|aioredis)\b", awaited=True),
        R("file", FS, path=r"^(io\.)?open$|^os\.(listdir|scandir|walk|stat|remove|unlink|rename|replace|makedirs|"
          r"mkdir|rmdir|fsync|read|write)$|^shutil\.\w+$", blocking=True),
        R("file", FS, methods={"read_text", "write_text", "read_bytes", "write_bytes"}, blocking=True),
        R("runtime", RUNTIME, path=r"^asyncio\.run$|(^|\.)run_until_complete$", blocking=True),
        R("cpu", CPU, path=r"^hashlib\.(pbkdf2_hmac|scrypt)$|^bcrypt\.(hashpw|checkpw|kdf)$|"
          r"^(argon2|passlib\.hash)\.\w+\.(hash|verify)$|^scrypt\.(hash|encrypt)$", blocking=True),
        # A concurrent.futures.Future's .result() or a threading.Thread's .join() called without going
        # through to_thread/run_in_executor: it blocks the calling thread until the future/thread finishes.
        R("future", WAIT, methods={"result"}, receiver=r"(?i)future|\bfut\b|\btask\b",
          imp=r"^concurrent\.futures\b", blocking=True),
        R("thread-join", WAIT, methods={"join"}, receiver=r"(?i)thread|\bt\b|\bth\b|\bworker\b",
          imp=r"^threading\b", blocking=True),
        R("api", NET, name=API_NAME, awaited=True),
    ],
    imports=imports,
    hooks=[locks, race_across_await, mutate_while_iterating, threads_and_fanout, uncommitted_write,
           unbounded_module_container, client_per_call, unchecked_response],
    implicit_calls=grpc_stream_reads,
    startup=re.compile(r"on_event\(\s*['\"]startup|\.on_startup\b|\blifespan\b"),
    stall_phrase="the asyncio event loop, freezing every coroutine",
    scip_indexer="scip-python",
)
