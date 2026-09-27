import os

PRICING_URL = os.environ.get("PRICING_URL", "http://pricing.internal:8080")
INVENTORY_URL = os.environ.get("INVENTORY_URL", "http://inventory.internal:8080")
REVIEWS_URL = os.environ.get("REVIEWS_URL", "http://reviews.internal:8080")
EVENTS_URL = os.environ.get("EVENTS_URL", "http://events.internal:8080")
DB_PATH = os.environ.get("ORDERDESK_DB", ":memory:")

INVENTORY_TIMEOUT = 10.0
INVENTORY_ATTEMPTS = 3
CHECKOUT_DEADLINE = 3.0
STOCK_DEADLINE = 2.0
STOCK_CACHE_TTL = 15.0

PRICE_FEED_INTERVAL = 30.0
PASSWORD_ITERATIONS = 390_000
QUOTE_RATE_PER_SECOND = 20

COUPONS = {
    "WELCOME-10": 10.0,
    "SPRING-SALE-15": 15.0,
    "VIP-GOLD-20": 20.0,
}
