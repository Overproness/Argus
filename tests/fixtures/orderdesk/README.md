# OrderDesk

Order and checkout API for a small housewares shop. It fronts three internal
services (pricing, inventory, reviews) and publishes order events.

## Layout

- `shop/api.py` — FastAPI routes and app lifespan
- `shop/services/` — orders, catalog, auth, event bus
- `shop/clients/` — HTTP clients for pricing, inventory and reviews
- `shop/db.py` — SQLite access
- `shop/worker.py` — price feed sync and reservation replay
- `shop/jobs.py` — a background job queue (`/batch`, `/jobs/{id}`); see `tests/eval/corpus.yaml`

## Run

```bash
pip install -r requirements.txt
PRICING_URL=... INVENTORY_URL=... uvicorn shop.api:app
```

## Test

```bash
pytest
```

The tests start a local fake upstream, so they need no network access.
