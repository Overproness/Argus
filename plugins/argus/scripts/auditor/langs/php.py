import re

from .base import DB, NET, SLEEP, LangSpec, R, text, walk


def imports(root):
    out = {}
    for n in walk(root):
        if n.type != "namespace_use_clause":
            continue
        s = text(n).strip().lstrip("\\")
        m = re.match(r"^(function\s+|const\s+)?([\w\\]+)(\s+as\s+(\w+))?$", s)
        if not m:
            continue
        full = m.group(2).replace("\\", ".")
        out[m.group(4) or full.split(".")[-1]] = full
        out["\0" + full] = full
    return out


TIMEOUT = re.compile(
    r"CURLOPT_TIMEOUT|CURLOPT_CONNECTTIMEOUT|['\"](connect_)?timeout['\"]\s*=>|->timeout\(|set_time_limit|default_socket_timeout"
)

SPEC = LangSpec(
    name="php",
    grammar="php",
    extensions=(".php",),
    function_types=frozenset({"function_definition", "method_declaration"}),
    call_types=frozenset({
        "function_call_expression", "member_call_expression", "scoped_call_expression",
        "nullsafe_member_call_expression",
    }),
    loop_types=frozenset({"for_statement", "foreach_statement", "while_statement", "do_statement"}),
    container_types=frozenset({"class_declaration", "trait_declaration", "interface_declaration", "enum_declaration"}),
    lambda_types=frozenset({"anonymous_function", "arrow_function", "anonymous_function_creation_expression"}),
    self_names=frozenset({"$this", "self", "static", "parent"}),
    iter_methods=frozenset({"array_map", "array_filter", "array_walk", "each", "map", "filter", "reduce",
                            "transform", "usort"}),
    file_is_module=False,
    arity_mode="no_max",
    timeout_text=TIMEOUT,
    client_timeout=re.compile(r"new\s+[\w\\]*Client\s*\(\s*\[[^\]]*timeout|->timeout\(|CURLOPT_TIMEOUT"),
    rules=[
        R("http", NET, path=r"^(curl_exec|curl_multi_exec)$", blocking=True),
        R("http", NET, path=r"^(file_get_contents|fopen|file)$", text=r"https?://|\$(url|uri|endpoint)", blocking=True,
          default_timeout="60s (default_socket_timeout)", conf="heuristic"),
        R("http", NET, methods={"get", "post", "put", "patch", "delete", "request", "send", "sendRequest", "requestAsync"},
          receiver=r"(?i)client|http|guzzle", imp=r"GuzzleHttp|Psr\.Http|Symfony\.Contracts\.HttpClient"),
        R("http", NET, path=r"^Http\.(get|post|put|patch|delete|send|withHeaders)$",
          default_timeout="30s (Laravel HTTP client default)"),
        R("db", DB, methods={"query", "exec", "execute", "prepare", "fetchAll", "fetch"},
          receiver=r"(?i)pdo|\bdb\b|stmt|conn|mysqli", conf="heuristic"),
        R("db", DB, path=r"^[A-Z]\w*\.(find|findOrFail|first|firstOrFail|all|create|count|pluck|exists)$", conf="heuristic"),
        R("db", DB, methods={"get", "first", "paginate", "count", "pluck", "exists", "sum", "firstOrFail"},
          receiver=r"\.(where|query|table|orderBy|with|join|whereIn)\b", conf="heuristic"),
        R("sleep", SLEEP, path=r"^(sleep|usleep)$", blocking=True),
    ],
    imports=imports,
    scip_indexer="scip-php",
)
