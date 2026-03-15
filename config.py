from __future__ import annotations

import os

# Core settings
DB_PATH = os.environ.get("FF14_DB_PATH", "db.sqlite")

# Data sources
UNIVERSALIS_BASE_URL = os.environ.get("UNIVERSALIS_BASE_URL", "https://universalis.app/api/v2")
XIVAPI_BASE_URL = os.environ.get("XIVAPI_BASE_URL", "https://xivapi.com")
XIVAPI_KEY = os.environ.get("XIVAPI_KEY")  # optional

# Market scopes for Universalis.
# `LOWEST_WORLD` is the aggregate scope used to discover the cheapest source world.
# `DISPLAY_WORLD` is the specific world shown for product pricing in the UI.
LOWEST_WORLD = os.environ.get("FF14_LOWEST_WORLD", "繁中服")
DISPLAY_WORLD = os.environ.get("FF14_DISPLAY_WORLD", "鳳凰")
WORLD = os.environ.get("FF14_WORLD", LOWEST_WORLD)

# Universalis batching / rate limiting
MAX_BATCH_SIZE = int(os.environ.get("FF14_MAX_BATCH_SIZE", "100"))
MAX_RPS = float(os.environ.get("FF14_MAX_RPS", "15"))  # <= 15 req/s
MAX_CONCURRENCY = int(os.environ.get("FF14_MAX_CONCURRENCY", "8"))

# Optional: extra item IDs to always refresh prices for
EXTRA_ITEM_IDS = [
    int(x)
    for x in os.environ.get("FF14_EXTRA_ITEM_IDS", "").split(",")
    if x.strip().isdigit()
]
