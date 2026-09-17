"""Patterns shared by several language specs."""
import re

# Awaited calls with API-ish names that don't resolve to repo code are probably remote.
API_NAME = re.compile(
    r"fetch|request|http|api|rpc|download|upload|subscribe|connect|"
    r"get_?(ticker|price|quote|order|orders|balance|book|position)",
    re.I,
)
CTOR = re.compile(r"^(new|builder|default|with_\w+|from_\w+|from|create|extend|Builder|New)$")
PY_CTOR = re.compile(r"^([A-Z]\w*|new|builder)$")
