import asyncio
import hashlib
import os
import sqlite3
import threading
import time
from typing import Any

from . import settings

_conn = sqlite3.connect(settings.DB_PATH, check_same_thread=False)
_conn.row_factory = sqlite3.Row
_lock = threading.Lock()

SCHEMA = """
create table if not exists categories (
    id integer primary key,
    name text not null,
    parent_id integer
);
create table if not exists products (
    sku text primary key,
    name text not null,
    price real not null,
    category_id integer references categories(id)
);
create table if not exists orders (
    id integer primary key,
    user_id text not null,
    total real not null,
    created_at real not null
);
create table if not exists order_lines (
    order_id integer references orders(id),
    sku text not null,
    qty integer not null,
    unit_price real not null
);
create table if not exists users (
    email text primary key,
    salt blob not null,
    pw_hash blob not null
);
"""

Row = dict[str, Any]


def _query(sql: str, params: tuple = ()) -> list[Row]:
    with _lock:
        return [dict(r) for r in _conn.execute(sql, params).fetchall()]


def _execute(sql: str, params: tuple = ()) -> int:
    with _lock:
        cur = _conn.execute(sql, params)
        _conn.commit()
        return cur.lastrowid or 0


def _executemany(sql: str, rows: list[tuple]) -> None:
    with _lock:
        _conn.executemany(sql, rows)
        _conn.commit()


async def query(sql: str, params: tuple = ()) -> list[Row]:
    return await asyncio.to_thread(_query, sql, params)


async def execute(sql: str, params: tuple = ()) -> int:
    return await asyncio.to_thread(_execute, sql, params)


async def get_product(sku: str) -> Row | None:
    rows = await query("select sku, name, price, category_id from products where sku = ?", (sku,))
    return rows[0] if rows else None


async def get_products(skus: list[str]) -> dict[str, Row]:
    if not skus:
        return {}
    placeholders = ",".join("?" for _ in skus)
    rows = await query(
        f"select sku, name, price, category_id from products where sku in ({placeholders})",
        tuple(skus),
    )
    return {r["sku"]: r for r in rows}


async def list_products() -> list[Row]:
    return await query("select sku, name, price, category_id from products")


async def search_products(term: str) -> list[Row]:
    return await query(f"select sku, name, price from products where name like '%{term}%' limit 50")


async def get_category(category_id: int) -> Row | None:
    rows = await query("select id, name, parent_id from categories where id = ?", (category_id,))
    return rows[0] if rows else None


async def create_order(user_id: str, total: float) -> int:
    return await execute(
        "insert into orders (user_id, total, created_at) values (?, ?, ?)",
        (user_id, total, time.time()),
    )


async def add_order_lines(order_id: int, lines: list[Row]) -> None:
    rows = [(order_id, l["sku"], l["qty"], l["unit_price"]) for l in lines]
    await asyncio.to_thread(
        _executemany,
        "insert into order_lines (order_id, sku, qty, unit_price) values (?, ?, ?, ?)",
        rows,
    )


async def get_order_lines(order_id: int) -> list[Row]:
    return await query("select sku, qty, unit_price from order_lines where order_id = ?", (order_id,))


async def update_prices(prices: dict[str, float]) -> None:
    await asyncio.to_thread(
        _executemany,
        "update products set price = ? where sku = ?",
        [(price, sku) for sku, price in prices.items()],
    )


def all_orders() -> list[Row]:
    return _query("select id, user_id, total, created_at from orders order by id")


async def get_user(email: str) -> Row | None:
    rows = await query("select email, salt, pw_hash from users where email = ?", (email,))
    return rows[0] if rows else None


def hash_password(password: str, salt: bytes) -> bytes:
    return hashlib.pbkdf2_hmac("sha256", password.encode(), salt, settings.PASSWORD_ITERATIONS)


def seed(n_products: int = 40) -> None:
    with _lock:
        _conn.executescript(SCHEMA)
        if _conn.execute("select count(*) from products").fetchone()[0]:
            return
        _conn.executemany(
            "insert into categories (id, name, parent_id) values (?, ?, ?)",
            [(1, "All", None), (2, "Kitchen", 1), (3, "Cookware", 2), (4, "Garden", 1)],
        )
        adjectives = ["steel", "cast iron", "ceramic", "bamboo", "copper"]
        nouns = ["pan", "pot", "kettle", "trowel", "planter", "whisk", "ladle", "shears"]
        _conn.executemany(
            "insert into products (sku, name, price, category_id) values (?, ?, ?, ?)",
            [
                (
                    f"SKU-{i}",
                    f"{adjectives[i % len(adjectives)]} {nouns[i % len(nouns)]}",
                    round(4.99 + i * 1.1, 2),
                    3 if nouns[i % len(nouns)] in ("pan", "pot", "kettle", "whisk", "ladle") else 4,
                )
                for i in range(n_products)
            ],
        )
        salt = os.urandom(16)
        _conn.execute(
            "insert into users (email, salt, pw_hash) values (?, ?, ?)",
            ("demo@orderdesk.test", salt, hash_password("correct horse", salt)),
        )
        _conn.commit()
