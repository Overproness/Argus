"""JavaScript, TypeScript and TSX share one spec shape."""
import re
from dataclasses import replace

from .base import DB, FS, NET, PROCESS, WAIT, LangSpec, R, text, walk
from .common import API_NAME, CTOR


def _module(s: str) -> str:
    s = s.strip("'\"`")
    return s[5:] if s.startswith("node:") else s


def imports(root):
    out = {}
    for n in walk(root):
        if n.type == "import_statement":
            src = n.child_by_field_name("source")
            if src is None:
                continue
            mod = _module(text(src))
            out["\0" + mod] = mod
            for clause in n.named_children:
                if clause.type != "import_clause":
                    continue
                for c in clause.named_children:
                    if c.type == "identifier":
                        out[text(c)] = mod
                    elif c.type == "namespace_import":
                        ids = [x for x in c.named_children if x.type == "identifier"]
                        if ids:
                            out[text(ids[-1])] = mod
                    elif c.type == "named_imports":
                        for spec in c.named_children:
                            if spec.type != "import_specifier":
                                continue
                            name = text(spec.child_by_field_name("name"))
                            alias = spec.child_by_field_name("alias")
                            out[text(alias) if alias else name] = f"{mod}.{name}"
        elif n.type == "variable_declarator":
            value = n.child_by_field_name("value")
            if value is None or value.type != "call_expression":
                continue
            if text(value.child_by_field_name("function")) != "require":
                continue
            args = value.child_by_field_name("arguments")
            strings = [a for a in (args.named_children if args else []) if a.type == "string"]
            if not strings:
                continue
            mod = _module(text(strings[0]))
            out["\0" + mod] = mod
            target = n.child_by_field_name("name")
            if target is not None and target.type == "identifier":
                out[text(target)] = mod
            elif target is not None and target.type == "object_pattern":
                for p in target.named_children:
                    if p.type == "shorthand_property_identifier_pattern":
                        out[text(p)] = f"{mod}.{text(p)}"
                    elif p.type == "pair_pattern":
                        key, val = p.child_by_field_name("key"), p.child_by_field_name("value")
                        out[text(val)] = f"{mod}.{text(key)}"
    return out


SPEC = LangSpec(
    name="javascript",
    grammar="javascript",
    extensions=(".js", ".mjs", ".cjs", ".jsx"),
    function_types=frozenset({"function_declaration", "generator_function_declaration", "method_definition"}),
    call_types=frozenset({"call_expression"}),
    loop_types=frozenset({"for_statement", "for_in_statement", "while_statement", "do_statement"}),
    container_types=frozenset({"class_declaration", "class", "abstract_class_declaration"}),
    module_types=frozenset({"internal_module"}),
    lambda_types=frozenset({"arrow_function", "function_expression", "function"}),
    named_lambda_parents=frozenset({
        "variable_declarator", "pair", "assignment_expression", "public_field_definition", "field_definition",
    }),
    await_types=frozenset({"await_expression"}),
    has_async=True,
    async_header=re.compile(r"\basync\b"),
    self_names=frozenset({"this"}),
    capital_is_type=True,
    arity_mode="no_min",
    iter_methods=frozenset({"map", "forEach", "filter", "reduce", "flatMap", "some", "every", "find", "findIndex", "sort"}),
    await_combinators=re.compile(r"(^|\.)(all|allSettled|race|any|pTimeout|timeout|withTimeout)$"),
    timeout_call=re.compile(r"(^|\.)(pTimeout|withTimeout|timeout)$"),
    timeout_text=re.compile(r"AbortSignal\.timeout|\btimeout\s*:|\bsignal\s*:|\.timeout\("),
    client_timeout=re.compile(r"(axios|got|ky)\.(create|extend)\(\s*\{[^}]*timeout|defaults\.timeout\s*="),
    rules=[
        R("fs", FS, path=r"^(fs|fs-extra|graceful-fs)\.\w+Sync$", blocking=True),
        R("process", PROCESS, path=r"^child_process\.(execSync|execFileSync|spawnSync)$", blocking=True),
        R("wait", WAIT, path=r"^Atomics\.wait$", blocking=True),
        R("db", DB, methods={"run", "get", "all", "exec", "iterate"}, receiver=r"(?i)stmt|db|statement|sqlite",
          imp=r"^better-sqlite3$", blocking=True),
        R("http", NET, path=r"^((window|globalThis|self)\.)?fetch$|^(node-fetch|undici|cross-fetch)(\.(fetch|request))?$",
          default_client=True),
        R("http", NET, path=r"^(axios|got|ky|superagent|needle)(\.\w+)?$", exclude_names=CTOR),
        R("http", NET, path=r"^(http|https)\.(get|request)$"),
        R("http", NET, methods={"get", "post", "put", "patch", "delete", "request", "head"},
          receiver=r"(?i)client|api|http|instance|axios", imp=r"^(axios|got|ky)$"),
        R("db", DB, methods={"findUnique", "findFirst", "findMany", "create", "createMany", "update", "updateMany",
                            "upsert", "delete", "deleteMany", "aggregate", "count", "groupBy", "$queryRaw", "$executeRaw"},
          receiver=r"(?i)prisma|db|tx", imp=r"^@prisma/client$"),
        R("db", DB, methods={"find", "findOne", "findById", "aggregate", "insertOne", "insertMany", "updateOne",
                            "updateMany", "deleteOne", "countDocuments", "exec", "bulkWrite"},
          imp=r"^(mongoose|mongodb)$"),
        R("db", DB, methods={"query", "execute", "raw", "findAll", "findOne", "findByPk", "save", "findOneBy", "getMany"},
          receiver=r"(?i)db|pool|client|conn|knex|repo|repository|sequelize|manager|model|\b[A-Z]\w*$",
          imp=r"^(pg|mysql2?(/promise)?|knex|sequelize|typeorm|kysely|drizzle-orm.*)$"),
        R("api", NET, name=API_NAME, awaited=True),
    ],
    imports=imports,
    stall_phrase="the Node.js event loop, so every in-flight request waits",
    scip_indexer="scip-typescript",
)

TS_SPEC = replace(SPEC, name="typescript", grammar="typescript", extensions=(".ts", ".mts", ".cts"))
TSX_SPEC = replace(SPEC, name="typescript", grammar="tsx", extensions=(".tsx",))
