import re

from .base import FS, NET, SLEEP, WAIT, LangSpec, R, text, walk


def imports(root):
    out = {}
    for n in walk(root):
        if n.type == "import_declaration":
            mod = re.sub(r"^\s*(@\w+\s+)*import\s+(\w+\s+)?", "", text(n)).strip()
            out["\0" + mod] = mod
    return out


SPEC = LangSpec(
    name="swift",
    grammar="swift",
    extensions=(".swift",),
    function_types=frozenset({"function_declaration"}),
    call_types=frozenset({"call_expression"}),
    loop_types=frozenset({"for_statement", "while_statement", "repeat_while_statement"}),
    container_types=frozenset({"class_declaration", "protocol_declaration"}),
    lambda_types=frozenset({"lambda_literal"}),
    file_is_module=False,
    await_types=frozenset({"await_expression"}),
    has_async=True,
    async_header=re.compile(r"\)\s*(async|throws\s+async|async\s+throws)\b|\basync\s*(throws\s*)?(->|\{|$)"),
    self_names=frozenset({"self", "super", "Self"}),
    iter_methods=frozenset({"map", "forEach", "filter", "compactMap", "flatMap", "reduce", "contains",
                            "first", "allSatisfy", "sorted"}),
    async_wrappers=re.compile(r"^(Task|Task\.detached)$"),
    timeout_text=re.compile(r"timeoutInterval|timeoutIntervalForRequest|timeoutIntervalForResource|requestModifier"),
    client_timeout=re.compile(r"timeoutIntervalForRequest|timeoutIntervalForResource|timeoutInterval\s*="),
    rules=[
        R("sleep", SLEEP, path=r"^(Thread\.sleep|sleep|usleep)$", blocking=True),
        R("wait", WAIT, methods={"wait"}, receiver=r"(?i)semaphore|group", blocking=True),
        R("fs", FS, path=r"^(Data|String|NSData|NSString)$", text=r"contentsOf", blocking=True),
        R("http", NET, methods={"data", "upload", "download", "bytes", "dataTask"},
          receiver=r"(?i)session|shared", default_timeout="60s per request (URLSession default)"),
        R("http", NET, path=r"^(AF\.request|Alamofire\.)"),
    ],
    imports=imports,
    stall_phrase="a Swift concurrency cooperative-pool thread",
)
