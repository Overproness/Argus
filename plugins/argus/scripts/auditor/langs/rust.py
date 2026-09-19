import re
from pathlib import PurePath

from ..model import HookHit
from .base import DB, FS, NET, PROCESS, RUNTIME, SLEEP, WAIT, LangSpec, R, expand_braced, text, walk
from .common import API_NAME, CTOR

REQWEST_BLOCKING = "30s (reqwest::blocking default, src/blocking/client.rs: Timeout(Some(Duration::from_secs(30))))"


def imports(root):
    out = {}
    for n in walk(root):
        if n.type == "use_declaration":
            for alias, full in expand_braced(text(n.child_by_field_name("argument")), "::"):
                out[alias] = full
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
    macro_awaits=re.compile(r"(^|::)(select|join|try_join|join_all)$"),
    imports=imports,
    hooks=[lock_across_await],
    module_of=module_of,
    stall_phrase="the tokio worker thread and every task scheduled on it",
    scip_indexer="rust-analyzer",
)
