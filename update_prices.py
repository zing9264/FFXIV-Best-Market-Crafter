from __future__ import annotations

import asyncio
import time
from collections import Counter
from typing import Callable, Iterable, List, Optional

import aiohttp

from config import (
    DISPLAY_WORLD,
    EXTRA_ITEM_IDS,
    LOWEST_WORLD,
    MAX_BATCH_SIZE,
    MAX_CONCURRENCY,
    MAX_RPS,
    UNIVERSALIS_BASE_URL,
    WORLD,
)
from db import get_conn, init_db


def _load_materia_item_ids() -> List[int]:
    """Pull materia item_ids out of data/materia_stats.csv.

    Most crafting materia (巨匠/名匠/魔匠 series) aren't ingredients in any
    recipe, so they wouldn't otherwise be touched by the full price refresh.
    Including them here keeps the materia optimizer's prices in sync with
    every full update. Returns an empty list if the CSV is missing.
    """
    import csv
    from pathlib import Path

    path = Path(__file__).resolve().parent / "data" / "materia_stats.csv"
    if not path.exists():
        return []
    ids: list[int] = []
    with path.open("r", encoding="utf-8", newline="") as handle:
        reader = csv.DictReader(handle)
        for row in reader:
            try:
                iid = int((row.get("item_id") or "").strip())
            except (TypeError, ValueError):
                continue
            if iid > 0:
                ids.append(iid)
    return ids


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
    ids.extend(_load_materia_item_ids())
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


def count_recent_sales(recent_history: list[dict], days: int = 3) -> int:
    if not isinstance(recent_history, list) or not recent_history:
        return 0
    now = int(time.time())
    cutoff = now - (days * 24 * 60 * 60)
    count = 0
    for row in recent_history:
        if not isinstance(row, dict):
            continue
        ts = normalize_timestamp(first_key(row, ["timestamp", "Timestamp"]))
        if ts is not None and ts >= cutoff:
            count += 1
    return count


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


def first_nested_key(values: Iterable[object], keys: Iterable[str]):
    for value in values:
        if isinstance(value, dict):
            result = first_key(value, keys)
            if result is not None:
                return result
    return None


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


async def fetch_prices(session: aiohttp.ClientSession, limiter: RateLimiter, ids: List[int], world: str):
    await limiter.wait()
    ids_param = ",".join(str(i) for i in ids)
    url = f"{UNIVERSALIS_BASE_URL}/{world}/{ids_param}"
    async with session.get(url, timeout=aiohttp.ClientTimeout(total=30)) as resp:
        resp.raise_for_status()
        return await resp.json()


async def fetch_history(session: aiohttp.ClientSession, limiter: RateLimiter, ids: List[int], world: str):
    await limiter.wait()
    ids_param = ",".join(str(i) for i in ids)
    url = f"{UNIVERSALIS_BASE_URL}/history/{world}/{ids_param}"
    async with session.get(url, timeout=aiohttp.ClientTimeout(total=30)) as resp:
        resp.raise_for_status()
        return await resp.json()


def build_price_row(price_item: dict, history_item: dict, world: str):
    item_id = first_key(price_item, ["itemID", "itemId", "item_id"]) or first_key(
        history_item, ["itemID", "itemId", "item_id"]
    )
    if not item_id:
        return None

    p50 = first_key(price_item, ["p50", "p50Price", "p50_price"])
    min_price = first_key(price_item, ["minPrice", "min_price"])
    listings = price_item.get("listings")
    if isinstance(listings, list):
        listings = len(listings)
    if listings is None:
        listings = price_item.get("listingsCount")

    history_entries = history_item.get("entries", []) or []
    daily_sales = count_recent_sales(history_entries, days=3)

    price_last_updated = normalize_timestamp(
        first_key(price_item, ["lastUploadTime", "lastUpload", "lastUpdated", "last_updated"])
    )
    history_last_updated = normalize_timestamp(
        first_key(history_item, ["lastUploadTime", "lastUpload", "lastUpdated", "last_updated"])
    )
    last_updated_candidates = [value for value in [price_last_updated, history_last_updated] if value is not None]
    last_updated = max(last_updated_candidates) if last_updated_candidates else None

    world_id = (
        price_item.get("worldID")
        or price_item.get("worldId")
        or history_item.get("worldID")
        or history_item.get("worldId")
    )
    world_name = first_key(price_item, ["worldName", "world", "world_name"])
    if not world_name:
        world_name = first_key(history_item, ["worldName", "world", "world_name"])
    if not world_name:
        world_name = first_nested_key(price_item.get("listings", []), ["worldName", "world_name"])

    sale_price = first_nested_key(
        history_entries,
        ["pricePerUnit", "price", "total"],
    )

    return (
        int(item_id),
        world,
        int(world_id) if isinstance(world_id, int) else 0,
        str(world_name),
        float(p50) if p50 is not None else 0,
        float(min_price) if min_price is not None else 0,
        float(sale_price) if sale_price is not None else 0,
        int(listings) if listings is not None else 0,
        float(daily_sales) if daily_sales is not None else 0,
        int(last_updated) if last_updated is not None else 0,
    )


async def fetch_batch_rows(
    session: aiohttp.ClientSession,
    limiter: RateLimiter,
    ids: List[int],
    world: str,
    retry_limit: int = 3,
    stats: Optional[Counter] = None,
):
    try:
        prices_payload, history_payload = await asyncio.gather(
            fetch_prices(session, limiter, ids, world),
            fetch_history(session, limiter, ids, world),
        )
        price_items = {
            int(item["itemID"]): item
            for item in extract_items(prices_payload)
            if isinstance(item, dict) and item.get("itemID")
        }
        history_items = {
            int(item["itemID"]): item
            for item in extract_items(history_payload)
            if isinstance(item, dict) and item.get("itemID")
        }
        rows = []
        for item_id in sorted(set(price_items) | set(history_items)):
            row = build_price_row(price_items.get(item_id, {}), history_items.get(item_id, {}), world)
            if row is not None:
                rows.append(row)
        return rows
    except aiohttp.ClientResponseError as exc:
        if stats is not None:
            stats[f"http_{exc.status}"] += 1
        if exc.status in {429, 500, 502, 503, 504}:
            if retry_limit > 0:
                if stats is not None:
                    stats["retries"] += 1
                await asyncio.sleep((4 - retry_limit) * 1.5 + 1)
                return await fetch_batch_rows(session, limiter, ids, world, retry_limit - 1, stats=stats)
            if len(ids) > 1:
                if stats is not None:
                    stats["splits"] += 1
                midpoint = len(ids) // 2
                left = await fetch_batch_rows(session, limiter, ids[:midpoint], world, 2, stats=stats)
                right = await fetch_batch_rows(session, limiter, ids[midpoint:], world, 2, stats=stats)
                return left + right
        raise
    except asyncio.TimeoutError:
        if stats is not None:
            stats["timeout"] += 1
        if retry_limit > 0:
            if stats is not None:
                stats["retries"] += 1
            await asyncio.sleep((4 - retry_limit) * 1.5 + 1)
            return await fetch_batch_rows(session, limiter, ids, world, retry_limit - 1, stats=stats)
        raise
    except aiohttp.ClientError:
        if stats is not None:
            stats["client_error"] += 1
        if retry_limit > 0:
            if stats is not None:
                stats["retries"] += 1
            await asyncio.sleep((4 - retry_limit) * 1.5 + 1)
            return await fetch_batch_rows(session, limiter, ids, world, retry_limit - 1, stats=stats)
        raise


async def update_prices_async(
    ids: Optional[List[int]] = None,
    world: str = WORLD,
    progress_callback: Optional[Callable[[dict], None]] = None,
    should_cancel: Optional[Callable[[], bool]] = None,
):
    if not ids:
        init_db()
        with get_conn() as conn:
            ids = get_item_ids(conn)

    if not ids:
        print("No item IDs found. Import recipes first.")
        return

    batches = batch_ids(ids, MAX_BATCH_SIZE)
    limiter = RateLimiter(MAX_RPS)
    sem = asyncio.Semaphore(MAX_CONCURRENCY)
    db_lock = asyncio.Lock()
    updated_rows = 0
    completed_batches = 0
    stats: Counter = Counter()

    if progress_callback:
        progress_callback(
            {
                "phase": "fetching_prices",
                "world": world,
                "total_ids": len(ids),
                "total_batches": len(batches),
                "completed_batches": 0,
                "updated_rows": 0,
                "stats": dict(stats),
            }
        )

    def persist_rows(rows: List[tuple]) -> None:
        if not rows:
            return
        with get_conn() as conn:
            cur = conn.cursor()
            cur.executemany(
                """
                INSERT OR REPLACE INTO prices(
                    item_id, world, world_id, world_name, p50_price, min_price, sale_price, listings, daily_sales, last_updated
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?);
                """,
                rows,
            )

    async with aiohttp.ClientSession() as session:
        async def worker(batch):
            nonlocal updated_rows, completed_batches
            if should_cancel and should_cancel():
                return
            async with sem:
                if should_cancel and should_cancel():
                    return
                rows = await fetch_batch_rows(session, limiter, batch, world, stats=stats)
                async with db_lock:
                    persist_rows(rows)
                    updated_rows += len(rows)
                    completed_batches += 1
                    if progress_callback:
                        progress_callback(
                            {
                                "phase": "fetching_prices",
                                "world": world,
                                "total_ids": len(ids),
                                "total_batches": len(batches),
                                "completed_batches": completed_batches,
                                "updated_rows": updated_rows,
                                "last_batch_size": len(batch),
                                "stats": dict(stats),
                            }
                        )

        tasks = [asyncio.create_task(worker(batch)) for batch in batches]
        await asyncio.gather(*tasks)

    print(f"Updated prices: {updated_rows} items for world {world}")
    if progress_callback:
        progress_callback(
            {
                "phase": "fetching_prices",
                "world": world,
                "total_ids": len(ids),
                "total_batches": len(batches),
                "completed_batches": completed_batches,
                "updated_rows": updated_rows,
                "stats": dict(stats),
            }
        )
    return updated_rows


async def update_all_prices_async(progress_callback: Optional[Callable[[dict], None]] = None) -> int:
    total = 0
    for world in [LOWEST_WORLD, DISPLAY_WORLD]:
        total += int(await update_prices_async(world=world, progress_callback=progress_callback) or 0)
    return total


def update_prices():
    asyncio.run(update_all_prices_async())


def update_prices_for_worlds(ids: List[int], worlds: List[str]) -> int:
    unique_worlds = [world for world in dict.fromkeys(worlds) if world]
    total = 0
    for world in unique_worlds:
        total += update_prices_for_ids(ids, world=world)
    return total


def update_prices_for_ids(ids: List[int], world: str = WORLD) -> int:
    unique_ids = sorted(set(int(item_id) for item_id in ids if int(item_id) > 0))
    if not unique_ids:
        return 0
    return int(asyncio.run(update_prices_async(unique_ids, world=world)) or 0)


if __name__ == "__main__":
    update_prices()
