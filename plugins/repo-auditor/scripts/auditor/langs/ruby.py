import re

from .base import DB, NET, SLEEP, LangSpec, R, text, walk


def imports(root):
    out = {}
    for n in walk(root):
        if n.type == "call" and text(n.child_by_field_name("method")) in ("require", "require_relative"):
            m = re.search(r"['\"]([^'\"]+)['\"]", text(n.child_by_field_name("arguments")))
            if m:
                out["\0" + m.group(1)] = m.group(1)
    return out


SPEC = LangSpec(
    name="ruby",
    grammar="ruby",
    extensions=(".rb", ".rake"),
    function_types=frozenset({"method", "singleton_method"}),
    call_types=frozenset({"call"}),
    loop_types=frozenset({"for", "while", "until", "while_modifier", "until_modifier"}),
    container_types=frozenset({"class", "singleton_class"}),
    module_types=frozenset({"module"}),
    lambda_types=frozenset({"block", "do_block", "lambda"}),
    file_is_module=False,
    self_names=frozenset({"self"}),
    iter_methods=frozenset({"each", "map", "each_with_index", "each_with_object", "select", "reject", "flat_map",
                            "find_each", "find_in_batches", "times", "inject", "reduce", "sum", "collect",
                            "filter_map", "each_slice", "upto", "downto", "any?", "all?"}),
    timeout_call=re.compile(r"(^|\.)Timeout\.timeout$"),
    timeout_text=re.compile(r"\btimeout\b|read_timeout|open_timeout"),
    client_timeout=re.compile(r"read_timeout\s*=|open_timeout\s*=|timeout:\s*\d|request\.timeout"),
    rules=[
        R("http", NET, path=r"^Net\.HTTP(\.\w+)*$", exclude_names=re.compile(r"^new$"),
          default_timeout="60s open/read (Net::HTTP defaults)"),
        R("http", NET, path=r"^(HTTParty|RestClient|Faraday|Excon|Typhoeus|HTTP)\.\w+$", exclude_names=re.compile(r"^new$")),
        R("http", NET, methods={"get", "post", "put", "patch", "delete", "request", "head"},
          receiver=r"(?i)conn|client|http|faraday|api", imp=r"faraday|net/http|httparty|excon|^http$"),
        R("http", NET, path=r"^URI\.open$"),
        R("sleep", SLEEP, path=r"^(Kernel\.)?sleep$", blocking=True),
        R("db", DB, path=r"^[A-Z]\w*\.(find|find_by!?|first|last|count|exists\?|create!?|update_all|destroy_all|"
                         r"pluck|sum|find_each|find_or_create_by!?|find_sole_by)$", conf="heuristic"),
        R("db", DB, methods={"pluck", "count", "exists?", "first", "last", "to_a", "sum", "find_each", "update_all"},
          receiver=r"\.(where|joins|includes|order|limit|scope|all)\b", conf="heuristic"),
    ],
    imports=imports,
    scip_indexer="scip-ruby",
)
