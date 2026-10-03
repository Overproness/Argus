from typing import Any

from .. import db


async def category_path(category_id: int) -> list[str]:
    category = await db.get_category(category_id)
    if category is None:
        return []
    if category["parent_id"] is None:
        return [category["name"]]
    return await category_path(category["parent_id"]) + [category["name"]]


def render_menu(node: dict[str, Any], depth: int = 0, max_depth: int = 6) -> list[str]:
    lines = ["  " * depth + node["name"]]
    if depth >= max_depth:
        return lines
    for child in node.get("children", []):
        lines.extend(render_menu(child, depth + 1, max_depth))
    return lines


def _tokens(name: str) -> set[str]:
    return set(name.lower().split())


def _similarity(a: dict, b: dict) -> float:
    ta, tb = _tokens(a["name"]), _tokens(b["name"])
    shared = len(ta & tb) / max(1, len(ta | tb))
    return shared + (0.5 if a["category_id"] == b["category_id"] else 0.0)


def related_products(sku: str, products: list[dict], limit: int = 5) -> list[str]:
    scores: dict[tuple[str, str], float] = {}
    for a in products:
        for b in products:
            if a["sku"] != b["sku"]:
                scores[(a["sku"], b["sku"])] = _similarity(a, b)
    ranked = sorted(((s, other) for (src, other), s in scores.items() if src == sku), reverse=True)
    return [other for _, other in ranked[:limit]]


DAYS = ["mon", "tue", "wed", "thu", "fri", "sat", "sun"]


def delivery_grid(blocked: set[tuple[str, int]]) -> dict[str, list[int]]:
    grid: dict[str, list[int]] = {}
    for day in DAYS:
        grid[day] = []
        for hour in range(24):
            if 8 <= hour < 20 and (day, hour) not in blocked:
                grid[day].append(hour)
    return grid
