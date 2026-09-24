"""Java."""
import re

from .base import DB, NET, RUNTIME, SLEEP, WAIT, LangSpec, R, expand_braced, text, walk

REPO_METHOD = r"^(find|get|read|query|count|exists|delete|stream)\w*By\w*$|^(findAll|findById|save|saveAll|deleteById|existsById|getReferenceById|saveAndFlush)$"


def _import_text(node) -> str:
    s = text(node)
    s = re.sub(r"^\s*import\s+(static\s+)?", "", s).rstrip(";").strip()
    return s


def _record(out: dict, spec: str):
    wild = re.sub(r"\s+", "", spec)
    if wild.endswith((".*", "._")):  # a wildcard import still tells library rules the package is in use
        out["\0" + wild[:-2]] = wild[:-2]
        return
    for alias, full in expand_braced(spec, "."):
        out[alias] = full
        out["\0" + full] = full


def java_imports(root):
    out = {}
    for n in walk(root):
        if n.type == "import_declaration":
            _record(out, _import_text(n))
    return out


JAVA_TIMEOUT = re.compile(
    r"\.timeout\(|setConnectTimeout|setReadTimeout|callTimeout|readTimeout|connectTimeout|"
    r"orTimeout|completeOnTimeout|responseTimeout|setQueryTimeout"
)

JAVA = LangSpec(
    name="java",
    grammar="java",
    extensions=(".java",),
    function_types=frozenset({"method_declaration", "constructor_declaration"}),
    call_types=frozenset({"method_invocation"}),
    loop_types=frozenset({"for_statement", "enhanced_for_statement", "while_statement", "do_statement"}),
    container_types=frozenset({"class_declaration", "interface_declaration", "enum_declaration", "record_declaration"}),
    lambda_types=frozenset({"lambda_expression"}),
    file_is_module=False,
    self_names=frozenset({"this", "super"}),
    iter_methods=frozenset({"forEach", "map", "filter", "flatMap", "reduce", "anyMatch", "allMatch", "noneMatch",
                            "removeIf", "peek", "mapToObj", "mapToInt"}),
    timeout_text=JAVA_TIMEOUT,
    client_timeout=JAVA_TIMEOUT,
    entry=re.compile(r"@(Get|Post|Put|Patch|Delete|Request)Mapping|@Scheduled|@\w*Listener|public\s+static\s+void\s+main"),
    rules=[
        R("http", NET, methods={"send", "sendAsync"}, receiver=r"(?i)client|http", imp=r"^java\.net\.http\b",
          client_level=False),
        R("http", NET, methods={"execute"}, receiver=r"(?i)newCall|call", imp=r"^okhttp3\b",
          default_timeout="10s connect/read/write (OkHttp default)"),
        R("http", NET, methods={"getForObject", "getForEntity", "postForObject", "postForEntity", "exchange",
                               "patchForObject", "delete", "put"},
          receiver=r"(?i)rest", imp=r"springframework\.web\.client"),
        R("http", NET, methods={"retrieve", "exchangeToMono", "exchangeToFlux"}, imp=r"reactive\.function\.client"),
        R("http", NET, methods={"openConnection", "getInputStream", "connect"}, receiver=r"(?i)url|conn",
          imp=r"^java\.net\.(URL|HttpURLConnection)$"),
        R("db", DB, methods={"executeQuery", "executeUpdate", "execute", "executeBatch"},
          receiver=r"(?i)stmt|statement|ps|prepared", imp=r"^java\.sql\b"),
        R("db", DB, name=REPO_METHOD, receiver=r"(?i)repo|repository|dao"),
        R("db", DB, methods={"createQuery", "createNativeQuery", "find", "persist", "merge", "getResultList", "getSingleResult"},
          receiver=r"(?i)\bem\b|entityManager|session|query", imp=r"persistence|hibernate"),
        R("sleep", SLEEP, path=r"(^|\.)Thread\.sleep$", blocking=True),
    ],
    imports=java_imports,
    scip_indexer="scip-java",
)
