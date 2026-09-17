import re

from ..model import HookHit
from .base import DB, NET, PROCESS, SLEEP, LangSpec, R, text, walk

GO_LOOPS = ("for_statement",)


def imports(root):
    out = {}
    for n in walk(root):
        if n.type != "import_spec":
            continue
        path = text(n.child_by_field_name("path")).strip('"`')
        name = n.child_by_field_name("name")
        alias = text(name) if name is not None else None
        if alias in ("_", "."):
            continue
        if alias is None:
            segs = [s for s in path.split("/") if not re.fullmatch(r"v\d+", s)]
            alias = segs[-1].replace("-", "_") if segs else path
        out[alias] = path
        out["\0" + path] = path
    return out


def receiver_info(fn_node):
    recv = fn_node.child_by_field_name("receiver")
    if recv is None:
        return None, None
    params = [p for p in recv.named_children if p.type == "parameter_declaration"]
    if not params:
        return None, None
    name = params[0].child_by_field_name("name")
    type_text = re.sub(r"\[.*?\]", "", text(params[0].child_by_field_name("type")))
    idents = re.findall(r"[A-Za-z_]\w*", type_text)
    return (text(name) if name is not None else None), (idents[-1] if idents else None)


def loop_hazards(spec, fn_node, body, is_async):
    hits = []
    for node in walk(body, lambda n: n.type in ("function_declaration", "method_declaration", "func_literal")):
        if node.type not in ("go_statement", "defer_statement"):
            continue
        n, in_loop = node.parent, False
        while n is not None and n.id != body.id:
            if n.type in GO_LOOPS:
                in_loop = True
                break
            if n.type == "func_literal":
                break
            n = n.parent
        if not in_loop:
            continue
        line = node.start_point[0] + 1
        if node.type == "go_statement":
            hits.append(HookHit(
                "unbounded-concurrency", "medium", line,
                "Goroutine started per loop iteration with no visible bound. Large inputs spawn "
                "unbounded goroutines and hammer downstream services. Use a worker pool or errgroup.SetLimit.",
            ))
        else:
            hits.append(HookHit(
                "defer-in-loop", "low", line,
                "`defer` inside a loop runs only when the function returns, so resources pile up across iterations.",
                confidence="exact",
            ))
    return hits


SPEC = LangSpec(
    name="go",
    grammar="go",
    extensions=(".go",),
    function_types=frozenset({"function_declaration", "method_declaration"}),
    call_types=frozenset({"call_expression"}),
    loop_types=frozenset(GO_LOOPS),
    lambda_types=frozenset({"func_literal"}),
    self_names=frozenset(),
    capital_is_type=False,
    file_is_module=False,
    private_of=lambda node, name, header: name[:1].islower(),  # package-private
    timeout_text=re.compile(r"WithTimeout|WithDeadline|Timeout\s*:|SetDeadline|SetReadDeadline|DialTimeout|\(\s*ctx\b"),
    client_timeout=re.compile(r"http\.Client\s*\{[^}]*Timeout\s*:|\.Timeout\s*=\s*"),
    rules=[
        R("http", NET, path=r"^net/http\.(Get|Post|PostForm|Head)$", default_client=True),
        R("http", NET, path=r"^net/http\.DefaultClient\.(Do|Get|Post|PostForm|Head)$", default_client=True),
        R("http", NET, methods={"Do", "Get", "Post", "PostForm", "Head"}, receiver=r"(?i)client|http", imp=r"^net/http$"),
        R("net", NET, path=r"^net\.(Dial|Listen)$"),
        R("db", DB, methods={"Query", "QueryRow", "Exec", "QueryContext", "QueryRowContext", "ExecContext", "Get", "Select", "Prepare"},
          receiver=r"(?i)db|tx|conn|pool|stmt|repo|store",
          imp=r"^(database/sql|github\.com/jmoiron/sqlx|github\.com/jackc/pgx.*|gorm\.io/gorm)$"),
        R("db", DB, methods={"Find", "First", "Create", "Save", "Delete", "Updates", "Take", "Last", "Count"},
          receiver=r"(?i)db|tx|gorm", imp=r"^gorm\.io/gorm$"),
        R("rpc", NET, name=r"^(Invoke|NewStream)$", imp=r"^google\.golang\.org/grpc$"),
        R("sleep", SLEEP, path=r"^time\.Sleep$", blocking=True),
        R("process", PROCESS, methods={"Run", "Output", "CombinedOutput", "Wait"}, receiver=r"(?i)cmd|exec",
          imp=r"^os/exec$", blocking=True),
    ],
    imports=imports,
    hooks=[loop_hazards],
    receiver_info=receiver_info,
    scip_indexer="scip-go",
)
