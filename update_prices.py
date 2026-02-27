from __future__ import annotations

import asyncio
import json
import math
import time
from typing import Iterable, List, Optional

import aiohttp

from config import (
    DB_PATH,
    EXTRA_ITEM_IDS,
    MAX_BATCH_SIZE,
    MAX_CONCURRENCY,
    MAX_RPS,
    UNIVERSALIS_BASE_URL,
    WORLD,
)
from db import get_conn, init_db


def get_item_ids(conn) -> List[int]:
    cur = conn.cursor()
    cur.execute(
        """
        SELECT DISTINCT output_item_id FROM recipes
        UNION
        SELECT DISTINCT ingredient_item_id FROM recipe_ingredients
        """
    )
    ids = [row[0] for row in cur.fetchall()]
    ids.extend(EXTRA_ITEM_IDS)
    return sorted(set(i for i in ids if isinstance(i, int) and i > 0))


def batch_ids(ids: List[int], size: int) -> List[List[int]]:
    return [ids[i : i + size] for i in range(0, len(ids), size)]


def normalize_timestamp(ts: Optional[int]) -> Optional[int]:
    if ts is None:
        return None
    if not isinstance(ts, (int, float)):
        return None
    ts = int(ts)
    # Convert ms to seconds if needed
    if ts > 1_000_000_000_000:
        return ts // 1000
    return ts


def first_key(d: dict, keys: Iterable[str]):
    for k in keys:
        if k in d and d[k] is not None:
            return d[k]
    return None


def extract_items(payload) -> List[dict]:
    if isinstance(payload, list):
        return payload
    if isinstance(payload, dict):
        items = payload.get("items")
        if isinstance(items, list):
            return items
        if isinstance(items, dict):
            return list(items.values())
        if "itemID" in payload or "itemId" in payload or "item_id" in payload:
            return [payload]
    return []


class RateLimiter:
    def __init__(self, rps: float):
        self.min_interval = 1.0 / rps if rps > 0 else 0
        self._lock = asyncio.Lock()
        self._last = 0.0

    async def wait(self):
        if self.min_interval <= 0:
            return
        async with self._lock:
            now = time.monotonic()
            delta = now - self._last
            if delta < self.min_interval:
                await asyncio.sleep(self.min_interval - delta)
            self._last = time.monotonic()


async def fetch_prices(session: aiohttp.ClientSession, limiter: RateLimiter, ids: List[int]):
    await limiter.wait()
    ids_param = ",".join(str(i) for i in ids)
    url = f"{UNIVERSALIS_BASE_URL}/{WORLD}/{ids_param}"
    async with session.get(url, timeout=30) as resp:
        resp.raise_for_status()
        return await resp.json()


async def update_prices_async():
    init_db()
    with get_conn() as conn:
        ids = get_item_ids(conn)

    if not ids:
        print("No item IDs found. Run update_recipes.py first.")
        return

    batches = batch_ids(ids, MAX_BATCH_SIZE)
    limiter = RateLimiter(MAX_RPS)
    sem = asyncio.Semaphore(MAX_CONCURRENCY)
    rows = []

    async with aiohttp.ClientSession() as session:
        async def worker(batch):
            async with sem:
                payload = await fetch_prices(session, limiter, batch)
                for item in extract_items(payload):
                    item_id = first_key(item, ["itemID", "itemId", "item_id"])
                    if not item_id:
                        continue

                    p50 = first_key(item, ["p50", "p50Price", "p50_price"])
                    min_price = first_key(item, ["minPrice", "min_price"])
                    listings = item.get("listings")
                    if isinstance(listings, list):
                        listings = len(listings)
                    if listings is None:
                        listings = item.get("listingsCount")

                    last_updated = normalize_timestamp(
                        first_key(item, ["lastUploadTime", "lastUpload", "lastUpdated", "last_updated"])
                    )

                    world_id = item.get("worldID") or item.get("worldId")

                    rows.append(
                        (
                            int(item_id),
                            WORLD,
                            int(world_id) if isinstance(world_id, int) else None,
                            float(p50) if p50 is not None else None,
                            float(min_price) if min_price is not None else None,
                            int(listings) if listings is not None else None,
                            int(last_updated) if last_updated is not None else None,
                        )
                    )

        tasks = [asyncio.create_task(worker(batch)) for batch in batches]
        await asyncio.gather(*tasks)

    with get_conn() as conn:
        cur = conn.cursor()
        cur.executemany(
            """
            INSERT OR REPLACE INTO prices(
                item_id, world, world_id, p50_price, min_price, listings, last_updated
            ) VALUES (?, ?, ?, ?, ?, ?, ?);
            """,
            rows,
        )

    print(f"Updated prices: {len(rows)} items for world {WORLD}")


def update_prices():
    asyncio.run(update_prices_async())


if __name__ == "__main__":
    update_prices()
