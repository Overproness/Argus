import re

from ..model import HookHit
from .base import DB, FS, NET, SLEEP, WAIT, LangSpec, R, text, walk


def imports(root):
    out = {}
    for n in walk(root):
        if n.type != "using_directive":
            continue
        s = re.sub(r"^\s*(global\s+)?using\s+(static\s+)?", "", text(n)).rstrip(";").strip()
        if "=" in s:
            alias, _, target = s.partition("=")
            out[alias.strip()] = target.strip()
        else:
            out["\0" + s] = s
    return out


def sync_over_async(spec, fn_node, body, is_async):
    """`task.Result`, `.Wait()` and `.GetAwaiter().GetResult()` block a thread on a task."""
    hits = []
    stop = lambda n: n.type in ("method_declaration", "local_function_statement")
    for node in walk(body, stop):
        if node.type != "member_access_expression":
            continue
        name = text(node.child_by_field_name("name"))
        parent = node.parent
        called = parent is not None and parent.type == "invocation_expression"
        if (name == "Result" and not called) or (name in ("Wait", "GetResult") and called):
            receiver = text(node.child_by_field_name("expression"))
            if name == "Result" and not re.search(r"(?i)task|async|\)$", receiver):
                continue
            hits.append(HookHit(
                "sync-over-async", "high" if is_async else "medium", node.start_point[0] + 1,
                f"`{text(node)[:80]}` blocks a thread waiting on a Task. Under load this starves the thread "
                "pool, and with a synchronization context it deadlocks. Use `await`.",
            ))
    return hits


TIMEOUT = re.compile(r"CancelAfter|\.Timeout\s*=|WaitAsync\(|TimeoutPolicy|Timeout\s*=\s*TimeSpan|AddStandardResilienceHandler")

SPEC = LangSpec(
    name="csharp",
    grammar="csharp",
    extensions=(".cs",),
    function_types=frozenset({"method_declaration", "local_function_statement", "constructor_declaration"}),
    call_types=frozenset({"invocation_expression"}),
    loop_types=frozenset({"for_statement", "foreach_statement", "while_statement", "do_statement"}),
    container_types=frozenset({"class_declaration", "struct_declaration", "interface_declaration", "record_declaration"}),
    file_is_module=False,
    lambda_types=frozenset({"lambda_expression", "anonymous_method_expression"}),
    await_types=frozenset({"await_expression"}),
    has_async=True,
    async_header=re.compile(r"\basync\b"),
    self_names=frozenset({"this", "base"}),
    iter_methods=frozenset({"Select", "Where", "ForEach", "SelectMany", "Aggregate", "Any", "All", "Sum",
                            "OrderBy", "GroupBy", "ToDictionary", "ForEachAsync"}),
    offload=re.compile(r"(^|\.)Task\.(Run|Factory\.StartNew)\s"),
    await_combinators=re.compile(r"(^|\.)(WhenAll|WhenAny|WaitAsync|ConfigureAwait)$"),
    timeout_call=re.compile(r"(^|\.)WaitAsync$"),
    timeout_text=TIMEOUT,
    client_timeout=TIMEOUT,
    entry=re.compile(r"\[(Http(Get|Post|Put|Delete|Patch)|Route|Function)\b|static\s+(async\s+)?\S+\s+Main\s*\("),
    rules=[
        R("sleep", SLEEP, path=r"(^|\.)Thread\.Sleep$", blocking=True),
        R("wait", WAIT, path=r"(^|\.)Task\.(WaitAll|WaitAny)$", blocking=True),
        R("http", NET, methods={"GetAsync", "PostAsync", "PutAsync", "DeleteAsync", "SendAsync", "PatchAsync",
                               "GetStringAsync", "GetStreamAsync", "GetByteArrayAsync", "GetFromJsonAsync",
                               "PostAsJsonAsync", "PutAsJsonAsync"},
          default_timeout="100s (HttpClient.Timeout default)"),
        R("http", NET, methods={"Send", "DownloadString", "UploadString", "GetResponse"},
          receiver=r"(?i)client|http|request|web", blocking=True, default_timeout="100s (HttpClient.Timeout default)"),
        R("db", DB, methods={"ExecuteReader", "ExecuteNonQuery", "ExecuteScalar", "Query", "QueryAsync", "Execute",
                            "ExecuteAsync", "QueryFirstOrDefault", "QueryFirstOrDefaultAsync", "ExecuteReaderAsync",
                            "ExecuteNonQueryAsync", "ExecuteScalarAsync", "QuerySingleAsync"},
          imp=r"Data\.SqlClient|Dapper|Npgsql|MySql|System\.Data"),
        R("db", DB, methods={"ToListAsync", "FirstOrDefaultAsync", "SingleOrDefaultAsync", "SaveChangesAsync",
                            "FindAsync", "AnyAsync", "CountAsync", "ToList", "FirstOrDefault", "SaveChanges", "Find",
                            "SingleOrDefault", "Count"},
          receiver=r"(?i)context|\bdb\b|_db|ctx|\bset\b|\.\w+s$", imp=r"EntityFrameworkCore"),
        R("fs", FS, path=r"(^|\.)File\.(ReadAll\w+|WriteAll\w+|AppendAll\w+|Copy|Move)$", blocking=True),
    ],
    imports=imports,
    hooks=[sync_over_async],
    stall_phrase="a thread-pool thread (sync-over-async), which starves the pool under load",
    scip_indexer="scip-dotnet",
)
