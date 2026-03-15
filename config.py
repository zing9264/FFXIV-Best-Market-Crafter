from __future__ import annotations

import os

# Core settings
DB_PATH = os.environ.get("FF14_DB_PATH", "db.sqlite")

# Data sources
UNIVERSALIS_BASE_URL = os.environ.get("UNIVERSALIS_BASE_URL", "https://universalis.app/api/v2")
XIVAPI_BASE_URL = os.environ.get("XIVAPI_BASE_URL", "https://xivapi.com")
XIVAPI_KEY = os.environ.get("XIVAPI_KEY")  # optional

# Target market scope for Universalis.
# This can be a single world (e.g. "Asura") or an aggregate scope (e.g. "繁中服", "Japan").
WORLD = os.environ.get("FF14_WORLD", "繁中服")

# Pricing behavior
# SELL_PRICE_FIELD: which field to use as "market成交價格"
# BUY_PRICE_FIELD: which field to use as "市場購買價"
SELL_PRICE_FIELD = os.environ.get("FF14_SELL_PRICE_FIELD", "p50")  # "p50" or "min"
BUY_PRICE_FIELD = os.environ.get("FF14_BUY_PRICE_FIELD", "min")    # "p50" or "min"

# Universalis batching / rate limiting
MAX_BATCH_SIZE = int(os.environ.get("FF14_MAX_BATCH_SIZE", "100"))
MAX_RPS = float(os.environ.get("FF14_MAX_RPS", "15"))  # <= 15 req/s
MAX_CONCURRENCY = int(os.environ.get("FF14_MAX_CONCURRENCY", "8"))

# Profit ranking
TOP_N = int(os.environ.get("FF14_TOP_N", "10"))
MIN_PROFIT = float(os.environ.get("FF14_MIN_PROFIT", "0"))
MAX_LISTINGS = int(os.environ.get("FF14_MAX_LISTINGS", "0"))  # 0 means no limit

# Optional: extra item IDs to always refresh prices for
EXTRA_ITEM_IDS = [
    int(x)
    for x in os.environ.get("FF14_EXTRA_ITEM_IDS", "").split(",")
    if x.strip().isdigit()
]
