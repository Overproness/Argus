import asyncio
import logging
from contextlib import asynccontextmanager

from fastapi import FastAPI, HTTPException
from pydantic import BaseModel

from . import db, jobs, settings, worker
from .cache import TTLCache
from .clients.inventory import InventoryClient
from .clients.pricing import PricingClient
from .clients.reviews import fetch_reviews
from .ratelimit import Throttle
from .services import auth, catalog
from .services.events import AuditLog, EventBus
from .services.orders import OrderService

log = logging.getLogger(__name__)

pricing = PricingClient()
inventory = InventoryClient()
events = EventBus()
audit = AuditLog()
orders = OrderService(pricing, inventory, events)
stock_cache = TTLCache(ttl=settings.STOCK_CACHE_TTL)
quote_throttle = Throttle(per_second=settings.QUOTE_RATE_PER_SECOND)

_background: set[asyncio.Task] = set()


def _spawn(coro) -> None:
    task = asyncio.create_task(coro)
    _background.add(task)

    def _done(t: asyncio.Task) -> None:
        _background.discard(t)
        if not t.cancelled() and t.exception() is not None:
            log.error("background task failed", exc_info=t.exception())

    task.add_done_callback(_done)


@asynccontextmanager
async def lifespan(_: FastAPI):
    db.seed()
    _spawn(events.run())
    _spawn(audit.run())
    _spawn(worker.price_feed_loop())
    yield
    for task in list(_background):
        task.cancel()
    await inventory.aclose()


app = FastAPI(title="OrderDesk", lifespan=lifespan)


class Line(BaseModel):
    sku: str
    qty: int


class CheckoutRequest(BaseModel):
    user_id: str
    lines: list[Line]
    coupon: str | None = None


class LoginRequest(BaseModel):
    email: str
    password: str


@app.get("/quote")
async def quote(skus: str) -> dict:
    quote_throttle.wait()
    return await orders.build_quote(skus.split(","))


@app.post("/checkout")
async def checkout(req: CheckoutRequest) -> dict:
    try:
        order_id, total = await orders.checkout(req.user_id, [l.model_dump() for l in req.lines], req.coupon)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc))
    return {"order_id": order_id, "total": total}


@app.get("/orders/{order_id}")
async def order_summary(order_id: int) -> dict:
    return {"order_id": order_id, "lines": await orders.order_summary(order_id)}


@app.get("/stock/{sku}")
async def stock(sku: str) -> dict:
    available = await asyncio.wait_for(
        stock_cache.get_or_load(sku, lambda: inventory.stock_level(sku)),
        timeout=settings.STOCK_DEADLINE,
    )
    return {"sku": sku, "available": available}


@app.get("/products")
async def products(skus: str) -> dict:
    found = await db.get_products(skus.split(","))
    return {"products": list(found.values())}


@app.get("/products/{sku}")
async def product_page(sku: str) -> dict:
    product = await db.get_product(sku)
    if product is None:
        raise HTTPException(status_code=404, detail="unknown sku")
    available = await inventory.stock_level(sku)
    reviews = await fetch_reviews(sku)
    live_price = await pricing.price_for_async(sku)
    return {**product, "price": live_price, "available": available, "reviews": reviews}


@app.get("/products/{sku}/related")
async def related(sku: str) -> dict:
    return {"sku": sku, "related": catalog.related_products(sku, await db.list_products())}


@app.get("/categories/{category_id}/path")
async def category_path(category_id: int) -> dict:
    return {"path": await catalog.category_path(category_id)}


@app.get("/search")
async def search(q: str) -> dict:
    return {"results": await db.search_products(q)}


@app.get("/delivery-slots")
async def delivery_slots() -> dict:
    return catalog.delivery_grid(blocked={("sun", 9), ("sun", 10)})


@app.post("/login")
async def login(req: LoginRequest) -> dict:
    token = await auth.login(req.email, req.password)
    await audit.record(f"login {'ok' if token else 'failed'} {req.email}")
    if token is None:
        raise HTTPException(status_code=401, detail="invalid credentials")
    return {"token": token}


@app.get("/admin/orders/export")
async def export_orders() -> dict:
    return {"orders": db.all_orders()}


class BatchRequest(BaseModel):
    order_ids: list[int]


@app.post("/batch")
async def batch(req: BatchRequest) -> dict:
    return {"job_ids": await jobs.submit_batch(req.order_ids)}


@app.get("/jobs/{job_id}")
async def job_status(job_id: str) -> dict:
    return await jobs.get_job(job_id)
