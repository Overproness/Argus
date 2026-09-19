"""C and C++."""
import re
from dataclasses import replace

from .base import DB, NET, PROCESS, SLEEP, WAIT, LangSpec, R, text, walk


def imports(root):
    out = {}
    for n in walk(root):
        if n.type == "preproc_include":
            inc = text(n.child_by_field_name("path")).strip('<>"')
            out["\0" + inc] = inc
    return out


TIMEOUT = re.compile(r"CURLOPT_(TIMEOUT|CONNECTTIMEOUT)|SO_RCVTIMEO|SO_SNDTIMEO|cpr::Timeout|\bpoll\(|\bselect\(|wait_for\(")

C = LangSpec(
    name="c",
    grammar="c",
    extensions=(".c", ".h"),
    function_types=frozenset({"function_definition"}),
    call_types=frozenset({"call_expression"}),
    loop_types=frozenset({"for_statement", "while_statement", "do_statement"}),
    sep="::",
    self_names=frozenset(),
    capital_is_type=False,
    timeout_text=TIMEOUT,
    client_timeout=TIMEOUT,
    rules=[
        R("http", NET, path=r"^curl_easy_perform$", blocking=True),
        R("net", NET, path=r"^(connect|recv|recvfrom|accept|gethostbyname|getaddrinfo)$", blocking=True),
        R("sleep", SLEEP, path=r"^(sleep|usleep|nanosleep|Sleep)$", blocking=True),
        R("db", DB, path=r"^(sqlite3_exec|sqlite3_step|PQexec|PQexecParams|mysql_query|mysql_real_query)$", blocking=True),
        R("process", PROCESS, path=r"^(system|popen)$", blocking=True),
    ],
    imports=imports,
    scip_indexer="scip-clang",
)

CPP = replace(
    C,
    name="cpp",
    grammar="cpp",
    extensions=(".cc", ".cpp", ".cxx", ".hpp", ".hh", ".hxx"),
    loop_types=frozenset({"for_statement", "while_statement", "do_statement", "for_range_loop"}),
    container_types=frozenset({"class_specifier", "struct_specifier"}),
    module_types=frozenset({"namespace_definition"}),
    lambda_types=frozenset({"lambda_expression"}),
    self_names=frozenset({"this"}),
    capital_is_type=True,
    iter_methods=frozenset({"for_each", "transform", "accumulate", "find_if", "count_if", "sort", "remove_if"}),
    rules=C.rules + [
        R("sleep", SLEEP, path=r"^std\.this_thread\.sleep_(for|until)$", blocking=True),
        R("wait", WAIT, methods={"get", "wait"}, receiver=r"(?i)fut|future|promise", blocking=True),
        R("http", NET, path=r"^cpr\.(Get|Post|Put|Delete|Patch|Head)$", blocking=True),
    ],
)
