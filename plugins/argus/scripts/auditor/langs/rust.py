import re
from pathlib import PurePath

from ..model import HookHit
from .base import DB, FS, NET, PROCESS, RUNTIME, SLEEP, WAIT, LangSpec, R, expand_braced, text, walk
from .common import API_NAME, CTOR

REQWEST_BLOCKING = "30s (reqwest::blocking default, src/blocking/client.rs: Timeout(Some(Duration::from_secs(30))))"
SOLANA_SYNC = r"^solana_(client|rpc_client)\.rpc_client\.RpcClient$"
SOLANA_ASYNC = r"^solana_(client|rpc_client)\.nonblocking\.rpc_client\.RpcClient$"
RPC_METHODS = frozenset({
    "get_slot", "get_slot_with_commitment", "get_account", "get_account_with_commitment",
    "get_account_data", "get_multiple_accounts", "get_multiple_accounts_with_commitment",
    "get_balance", "get_balance_with_commitment", "get_transaction", "get_transaction_with_config",
    "get_block", "get_block_with_config", "get_block_height", "get_latest_blockhash",
    "get_latest_blockhash_with_commitment", "get_signature_status", "get_signature_statuses",
    "get_signature_statuses_with_history", "get_token_account_balance", "get_token_supply",
    "get_token_accounts_by_owner", "get_token_largest_accounts", "get_signatures_for_address",
    "get_signatures_for_address_with_config",
    "get_program_accounts", "get_program_accounts_with_config", "get_recent_prioritization_fees",
    "send_transaction", "send_transaction_with_config", "send_and_confirm_transaction",
    "send_and_confirm_transaction_with_spinner", "confirm_transaction", "request_airdrop",
    "simulate_transaction", "simulate_transaction_with_config", "send",
})
RPC_TIMEOUT_NOTE = ("No deadline on the whole RPC operation was identified. The SDK's HTTP timeout is per "
                    "attempt; retries and confirmation polling can exceed it. Verify the configured transport "
                    "and wrap the operation in a deadline if its total wait must be bounded.")


def imports(root):
    out = {}
    for n in walk(root):
        if n.type == "use_declaration":
            for alias, full in expand_braced(text(n.child_by_field_name("argument")), "::"):
                out[alias] = full
        elif n.type == "macro_definition":
            out[text(n.child_by_field_name("name"))] = "local_macro"
    return out


def macro_await_args(root):
    """Simple local macros that await their expression parameters, e.g. RPC fallback.

    Require every arm to agree; repeated token captures and complex patterns
    are left to a compiler index. Never infer polling from a macro's name.
    """
    result = {}
    for node in walk(root):
        if node.type != "macro_definition":
            continue
        arms = []
        for arm in node.named_children:
            if arm.type != "macro_rule":
                continue
            pattern = text(arm.child_by_field_name("left"))
            expansion_node = arm.child_by_field_name("right")
            expansion_bytes = bytearray(expansion_node.text) if expansion_node is not None else bytearray()
            for literal in walk(expansion_node) if expansion_node is not None else []:
                if literal.type in {"string_literal", "raw_string_literal", "line_comment", "block_comment"}:
                    lo, hi = literal.start_byte - expansion_node.start_byte, literal.end_byte - expansion_node.start_byte
                    expansion_bytes[lo:hi] = b" " * (hi - lo)
            expansion = expansion_bytes.decode("utf8", "replace")
            parameters = re.findall(r"\$(\w+)\s*:\s*expr\b", pattern)
            if not parameters or len(parameters) != pattern.count("$"):
                arms.append(set())
                continue
            arms.append({i for i, name in enumerate(parameters)
                         if re.search(r"\$" + re.escape(name) + r"\s*\.\s*await\b", expansion)})
        if arms and all(a == arms[0] for a in arms):
            result[text(node.child_by_field_name("name"))] = arms[0]
    return result


def canonical_type(s, imported):
    s = re.sub(r"^&\s*(?:'\w+\s*)?(?:mut\s+)?", "", s.strip())
    wrapper = re.fullmatch(r"(?:[\w:]+::)?(Option|Arc|Box|Mutex|RwLock)<(.+)>", s)
    if wrapper:
        return canonical_type(wrapper.group(2), imported)
    if not re.fullmatch(r"\w+(?:::\w+)*", s):
        return None
    parts = s.replace("::", ".").split(".")
    return ".".join([imported.get(parts[0], parts[0]), *parts[1:]])


def field_types(root, imported):
    out = {}
    for node in walk(root):
        if node.type != "struct_item":
            continue
        name = text(node.child_by_field_name("name"))
        fields = {}
        for field in walk(node):
            if field.type == "field_declaration":
                typ = canonical_type(text(field.child_by_field_name("type")), imported)
                if typ:
                    fields[text(field.child_by_field_name("name"))] = typ
        if name in out:  # duplicate names in nested modules need a compiler index
            out[name] = {}
        else:
            out[name] = fields
    return out


def bindings(fn_node, body, imported):
    """Positive local type evidence; unknown bindings deliberately shadow known ones.

    This is not Rust type inference. It follows annotated arguments/fields, known
    constructors and identity wrappers, preserving lexical scope and shadowing.
    """
    out = {}

    def canonical(s):
        return canonical_type(s, imported)

    def add(key, start, end, typ):
        out.setdefault(key, []).append((start, end, typ))

    def lookup(key, byte):
        prior = [v for v in out.get(key, []) if v[0] <= byte < v[1]]
        return max(prior, key=lambda v: v[0])[2] if prior else None

    def value_type(n):
        if n is None:
            return None
        if n.type in ("identifier", "field_expression"):
            return lookup(text(n).replace(" ", ""), n.start_byte)
        if n.type in ("reference_expression", "try_expression", "parenthesized_expression"):
            return value_type(n.named_children[0]) if n.named_children else None
        if n.type != "call_expression":
            return None
        func = n.child_by_field_name("function")
        if func is None:
            return None
        if func.type == "field_expression":
            method = text(func.child_by_field_name("field"))
            if method in {"unwrap", "expect", "clone", "as_ref", "as_mut", "lock"}:
                return value_type(func.child_by_field_name("value"))
            return None
        path = canonical(text(func)) or ""
        owner, _, method = path.rpartition(".")
        if re.fullmatch(SOLANA_SYNC, owner) or re.fullmatch(SOLANA_ASYNC, owner):
            return owner if method == "new" or method.startswith("new_with_") else None
        if owner == "std.fs.File" and method in {"open", "create", "create_new", "options"}:
            return owner if method != "options" else None
        if path in {"std.io.stdout", "std.io.stderr"}:
            return "std.io." + ("Stdout" if path.endswith("stdout") else "Stderr")
        return None

    params = fn_node.child_by_field_name("parameters")
    for p in params.named_children if params is not None else []:
        if p.type == "parameter":
            name = text(p.child_by_field_name("pattern")).removeprefix("mut ")
            add(name, fn_node.start_byte, fn_node.end_byte, canonical(text(p.child_by_field_name("type"))))
    owner = fn_node.parent.parent if fn_node.parent is not None else None
    if owner is not None and owner.type == "impl_item":
        struct_name = text(owner.child_by_field_name("type"))
        scope = owner.parent
        for s in scope.named_children if scope is not None else []:
            if s.type == "struct_item" and text(s.child_by_field_name("name")) == struct_name:
                for field in walk(s):
                    if field.type == "field_declaration":
                        add("self." + text(field.child_by_field_name("name")), fn_node.start_byte,
                            fn_node.end_byte, canonical(text(field.child_by_field_name("type"))))
    for n in walk(body, lambda n: n.type in {"function_item", "impl_item"}):
        if n.type == "let_declaration":
            name = text(n.child_by_field_name("pattern")).removeprefix("mut ")
            if re.fullmatch(r"\w+", name):
                scope = n.parent
                typ = n.child_by_field_name("type")
                add(name, n.end_byte, scope.end_byte, canonical(text(typ)) if typ else value_type(n.child_by_field_name("value")))
        elif n.type == "assignment_expression":
            left = text(n.child_by_field_name("left"))
            if re.fullmatch(r"\w+(?:\.\w+)*", left):
                # A later untyped assignment invalidates earlier constructor evidence.
                add(left, n.end_byte, body.end_byte, value_type(n.child_by_field_name("right")))
        elif n.type == "match_arm":
            pattern = text(n.child_by_field_name("pattern"))
            match = re.fullmatch(r"Some\(\s*(?:ref\s+)?(?:mut\s+)?(\w+)\s*\)", pattern)
            owner = n.parent.parent if n.parent is not None else None
            if match and owner is not None and owner.type == "match_expression":
                add(match.group(1), n.start_byte, n.end_byte, value_type(owner.child_by_field_name("value")))
    return out


def module_of(rel: PurePath, root_name: str) -> tuple[str, ...]:
    parts = list(rel.parts)
    if "src" in parts:
        idx = len(parts) - 1 - parts[::-1].index("src")
        crate = parts[idx - 1] if idx > 0 else root_name
        mods = parts[idx + 1:]
    else:
        crate, mods = root_name, parts
    mods = [m[:-3] if m.endswith(".rs") else m for m in mods]
    if mods and mods[-1] in ("lib", "main", "mod"):
        mods = mods[:-1]
    return (crate.replace("-", "_"), *mods)


def is_private(node, name, header) -> bool:
    """Non-`pub` items are module-private, except trait methods (public through the trait)."""
    if re.match(r"\s*pub\b", header):
        return False
    owner = node.parent.parent if node.parent is not None else None
    if owner is not None and (owner.type == "trait_item" or (
            owner.type == "impl_item" and owner.child_by_field_name("trait") is not None)):
        return False
    return True


def lock_across_await(spec, fn_node, body, is_async):
    """A std/parking_lot guard bound with `let` that is still alive at a later `.await`."""
    hits = []
    for node in walk(body, lambda n: n.type == "function_item"):
        if node.type != "let_declaration":
            continue
        value = text(node.child_by_field_name("value"))
        if not re.search(r"\.(lock|read|write)\(\)", value) or ".await" in value:
            continue
        if not (is_async or _inside(node, "async_block", body)):
            continue
        guard = text(node.child_by_field_name("pattern")).replace("mut ", "").strip()
        sib = node.next_named_sibling
        while sib is not None:
            if re.search(rf"drop\(\s*{re.escape(guard)}\s*\)", text(sib)):
                break
            if any(d.type == "await_expression" for d in walk(sib, lambda n: n.type == "closure_expression")):
                hits.append(HookHit(
                    "lock-across-await", "medium", node.start_point[0] + 1,
                    f"Sync lock guard `{guard}` is still alive at a later `.await`. It blocks other "
                    "threads for the whole await and makes the future !Send. Drop it first or use tokio::sync.",
                ))
                break
            sib = sib.next_named_sibling
    return hits


def _inside(node, typ, stop):
    n = node.parent
    while n is not None and n.id != stop.id:
        if n.type == typ:
            return True
        n = n.parent
    return False


def retry_policy_bound(loop_node, body, hint):
    """A counter limit on one error branch doesn't bound a different retry branch.

    Ignore nested loops and closures. To trust a counter on a `continue` path,
    both its progress and its exit check must be reached before that back edge.
    This deliberately errs toward an investigation lead when control flow is
    complex, instead of asserting a finite worst-case wait from a text match.
    """
    if not hint:
        return False
    nodes = list(walk(body, lambda n: n.type in {
        "function_item", "closure_expression", "async_block", "loop_expression", "while_expression", "for_expression"}))
    guards = []
    for n in nodes:
        if n.type != "if_expression":
            continue
        condition = text(n.child_by_field_name("condition"))
        match = re.search(r"(?i)\b(\w*(?:attempt|retr(?:y|ies)|tries)\w*)\s*(?:>=?|==)\s*[\w:]+", condition)
        consequence = n.child_by_field_name("consequence")
        if match and consequence is not None and any(c.type in {"break_expression", "return_expression"} for c in walk(consequence)):
            guards.append((n, match.group(1)))
    if not guards:
        return hint

    def dominates(earlier, later):
        scope = earlier.parent
        while scope is not None and scope.id != body.id and scope.type != "block":
            scope = scope.parent
        return (earlier.end_byte <= later.start_byte and scope is not None
                and scope.start_byte <= later.start_byte < scope.end_byte)

    for back_edge in [n for n in nodes if n.type == "continue_expression"]:
        # Labeled continues may target an outer loop: don't assume this counter bounds it.
        if text(back_edge).strip() != "continue":
            return False
        bounded = False
        for guard, counter in guards:
            updates = [n for n in nodes if n.type in {"compound_assignment_expr", "assignment_expression"}
                       and text(n.child_by_field_name("left")) == counter
                       and re.search(r"\+=\s*[1-9]|=\s*" + re.escape(counter) + r"\s*\+\s*[1-9]", text(n))]
            if dominates(guard, back_edge) and any(dominates(update, back_edge) for update in updates):
                bounded = True
                break
        if not bounded:
            return False
    return True


SPEC = LangSpec(
    name="rust",
    grammar="rust",
    extensions=(".rs",),
    function_types=frozenset({"function_item"}),
    call_types=frozenset({"call_expression"}),
    loop_types=frozenset({"for_expression", "while_expression", "loop_expression"}),
    container_types=frozenset({"impl_item", "trait_item"}),
    module_types=frozenset({"mod_item"}),
    lambda_types=frozenset({"closure_expression"}),
    await_types=frozenset({"await_expression"}),
    async_scope_types=frozenset({"async_block"}),
    has_async=True,
    async_header=re.compile(r"\basync\b"),
    sep="::",
    iter_methods=frozenset({
        "map", "for_each", "filter", "filter_map", "flat_map", "fold", "try_fold",
        "try_for_each", "any", "all", "find", "find_map", "retain", "position",
        "sort_by", "sort_by_key", "sort_unstable_by", "sort_unstable_by_key",
    }),
    offload=re.compile(r"(^|\.)(spawn_blocking|block_in_place)\s|(^|\.)thread\.spawn\s"),
    await_combinators=re.compile(r"(^|\.)(timeout|timeout_at|join_all|try_join_all|select_all)$"),
    timeout_call=re.compile(r"(^|\.)timeout(_at)?$"),
    timeout_text=re.compile(r"\.timeout\("),
    client_timeout=re.compile(r"builder\(\)[\s\S]*\.timeout\("),
    entry=re.compile(r"#\[(tokio|actix_web|async_std)::main"),
    rules=[
        R("http", NET, path=r"^reqwest\.blocking\.", blocking=True, exclude_names=CTOR, default_timeout=REQWEST_BLOCKING),
        R("http", NET, path=r"^(ureq|attohttpc|minreq)\.", blocking=True, exclude_names=CTOR),
        R("http", NET, methods={"send", "execute"}, imp=r"^reqwest\.blocking\b", blocking=True, default_timeout=REQWEST_BLOCKING),
        R("http", NET, methods={"call", "send"}, imp=r"^(ureq|attohttpc|minreq)\b", blocking=True),
        R("sleep", SLEEP, path=r"^(std\.)?thread\.sleep$", blocking=True),
        R("fs", FS, path=r"^std\.fs\.", blocking=True, exclude_names=CTOR),
        R("stdio", FS, path=r"^std\.(print|println|eprint|eprintln)$", blocking=True),
        R("stdio", FS, path=r"^std\.io\.(stdout|stderr)(\.lock)?\.(write|write_all|write_fmt|flush)$", blocking=True),
        R("fs", FS, methods={"read", "read_exact", "read_to_end", "read_to_string", "write", "write_all", "write_fmt", "flush", "sync_all", "sync_data"},
          receiver_type=r"^std\.(fs\.File|io\.(Stdout|Stderr)(Lock)?)$", blocking=True, conf="exact"),
        # The SDK's HTTP timeout is per attempt. Transport retries/confirmation
        # polls can exceed it; require a deadline on the whole RPC operation.
        R("rpc", NET, methods=RPC_METHODS, receiver_type=SOLANA_SYNC, blocking=True, conf="exact", client_level=False,
          timeout_note=RPC_TIMEOUT_NOTE),
        R("rpc", NET, methods=RPC_METHODS, receiver_type=SOLANA_ASYNC, awaited=True, conf="exact", client_level=False,
          timeout_note=RPC_TIMEOUT_NOTE),
        R("net", NET, path=r"^std\.net\.(TcpStream|TcpListener|UdpSocket)\.", blocking=True, exclude_names=CTOR),
        R("dns", NET, methods={"to_socket_addrs"}, blocking=True, conf="heuristic"),
        R("process", PROCESS, path=r"^std\.process\.Command\b.*\.(output|status|wait)$", blocking=True),
        R("block_on", RUNTIME, path=r"(^|\.)block_on$", blocking=True),
        R("channel", WAIT, methods={"recv"}, imp=r"^(std\.sync\.mpsc|crossbeam)", blocking=True),
        R("http", NET, path=r"^(reqwest|hyper)\.", awaited=True, exclude_names=CTOR),
        R("http", NET, methods={"send", "execute", "request"}, imp=r"^(reqwest|hyper)\b", awaited=True),
        R("db", DB, path=r"^(sqlx|redis|mongodb|tokio_postgres|diesel_async)\.", awaited=True),
        R("db", DB, methods={"fetch_one", "fetch_all", "fetch_optional", "query", "execute", "query_as"},
          imp=r"^(sqlx|tokio_postgres|redis|mongodb|sea_orm)\b", awaited=True),
        R("rpc", NET, path=r"^(tonic|tokio_tungstenite)\.", awaited=True, exclude_names=CTOR),
        R("net", NET, path=r"^tokio\.net\.(TcpStream|UnixStream)\.connect$|(^|\.)connect_async$", awaited=True),
        R("api", NET, name=API_NAME, awaited=True),
    ],
    private_of=is_private,
    macro_types=frozenset({"macro_invocation"}),
    macro_awaits=re.compile(r"(^|\.)(select|join|try_join|join_all)$"),
    macro_await_args=macro_await_args,
    imports=imports,
    bindings=bindings,
    field_types=field_types,
    retry_policy_bound=retry_policy_bound,
    builtin_io_macros=frozenset({"std.print", "std.println", "std.eprint", "std.eprintln"}),
    hooks=[lock_across_await],
    module_of=module_of,
    stall_phrase="the tokio worker thread and every task scheduled on it",
    scip_indexer="rust-analyzer",
)
