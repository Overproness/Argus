import re

from ..model import HookHit
from .base import DB, NET, PROCESS, RUNTIME, SLEEP, LangSpec, R, text, walk
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


def lock_in_async(spec, fn_node, body, is_async):
    """`with some_lock:` (a threading lock) wrapping an `await` inside a coroutine."""
    if not is_async:
        return []
    hits = []
    for node in walk(body, lambda n: n.type in ("function_definition", "lambda")):
        if node.type != "with_statement" or text(node).startswith("async"):
            continue
        items = " ".join(text(c) for c in node.named_children if c.type == "with_clause")
        if not re.search(r"(?i)lock|mutex|semaphore", items):
            continue
        body_node = node.child_by_field_name("body")
        if body_node is not None and any(d.type == "await" for d in walk(body_node)):
            hits.append(HookHit(
                "lock-across-await", "medium", node.start_point[0] + 1,
                f"Sync lock (`with {items[:60]}`) held across `await`. Other threads block for the whole "
                "await, and another coroutine taking the same lock deadlocks the loop. Use asyncio.Lock.",
            ))
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
        R("db", DB, path=r"^(psycopg2?|pymysql|sqlite3|MySQLdb|pymongo|redis)\.", blocking=True, exclude_names=PY_CTOR),
        R("db", DB, methods={"execute", "executemany", "fetchall", "fetchone", "commit"},
          imp=r"^(psycopg2?|pymysql|sqlite3|MySQLdb|sqlalchemy|django\.db)\b", blocking=True),
        R("db", DB, methods={"get", "first", "count", "exists", "create", "save", "delete", "update", "aggregate", "one", "scalar"},
          receiver=r"\.objects\b|\.query\b|(?i:session)", imp=r"^(django|sqlalchemy|flask_sqlalchemy)\b", blocking=True),
        R("db", DB, methods={"execute", "fetch", "fetchrow", "fetchval", "find_one", "insert_one", "get", "set"},
          receiver=r"(?i)conn|pool|db|redis|collection|session",
          imp=r"^(asyncpg|motor|databases|sqlalchemy\.ext\.asyncio|redis\.asyncio|aioredis)\b", awaited=True),
        R("runtime", RUNTIME, path=r"^asyncio\.run$|(^|\.)run_until_complete$", blocking=True),
        R("api", NET, name=API_NAME, awaited=True),
    ],
    imports=imports,
    hooks=[lock_in_async],
    stall_phrase="the asyncio event loop, freezing every coroutine",
    scip_indexer="scip-python",
)
