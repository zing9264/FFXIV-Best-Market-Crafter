"""從 Universalis 抓取價格並寫入 prices 表。

2026-08 改版:改用 `/api/v2/aggregated/{world}/{ids}` 端點。
- 舊版的區域聚合端點(`/繁中服/{ids}`)在 Universalis 端長期 504,
  每一批都要靠重試硬磨,全量更新動輒數小時。
- aggregated 端點以「世界」層級查詢時,單次回應同時帶 world/dc/region
  三層資料 —— 一批一次呼叫就能同時取得「區域最低價(含來源伺服器)」
  與「顯示伺服器價格」,請求數砍半、單次延遲從 10 秒級降到 1 秒內。
- 代價:aggregated 不提供掛單數(listings),該欄位固定寫 0;
  「近三天成交筆數」改以 dailySaleVelocity * 3 估算。
"""
from __future__ import annotations

import asyncio
import csv
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
    """Pull materia item_ids out of data/materia_stats.csv."""
    try:
        from materia_optimizer import MATERIA_STATS_PATH
    except Exception:
        return []
    ids: List[int] = []
    try:
        with open(MATERIA_STATS_PATH, "r", encoding="utf-8-sig", newline="") as f:
            reader = csv.DictReader(f)
            for row in reader:
                try:
                    iid = int((row.get("item_id") or "").strip())
                except (TypeError, ValueError):
                    continue
                if iid > 0:
                    ids.append(iid)
    except OSError:
        return []
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
    ids = [int(row[0]) for row in cur.fetchall() if row[0]]
    ids.extend(_load_materia_item_ids())
    ids.extend(EXTRA_ITEM_IDS)
    return sorted(set(i for i in ids if i > 0))


def batch_ids(ids: List[int], size: int) -> List[List[int]]:
    return [ids[i : i + size] for i in range(0, len(ids), size)]


def normalize_timestamp(ts: Optional[int]) -> Optional[int]:
    if ts is None:
        return None
    try:
        value = int(ts)
    except (TypeError, ValueError):
        return None
    if value > 10**12:  # 毫秒 -> 秒
        value //= 1000
    return value


class RateLimiter:
    def __init__(self, rps: float):
        self._interval = 1.0 / rps if rps > 0 else 0
        self._last = 0.0
        self._lock = asyncio.Lock()

    async def wait(self):
        if self._interval <= 0:
            return
        async with self._lock:
            now = time.monotonic()
            delay = self._interval - (now - self._last)
            if delay > 0:
                await asyncio.sleep(delay)
            self._last = time.monotonic()


async def fetch_marketable_ids(session: aiohttp.ClientSession) -> Optional[set[int]]:
    """Universalis 的可交易道具清單。不可交易的 id 會讓 aggregated 整批 400。"""
    try:
        async with session.get(
            f"{UNIVERSALIS_BASE_URL}/marketable", timeout=aiohttp.ClientTimeout(total=15)
        ) as resp:
            resp.raise_for_status()
            payload = await resp.json()
            return {int(i) for i in payload}
    except Exception:
        return None  # 拿不到就不過濾,靠 400 對切降級


async def fetch_world_names(session: aiohttp.ClientSession) -> dict[int, str]:
    """抓 world_id -> 名稱對照(區域最低價要標示來源伺服器)。失敗回空表。"""
    try:
        async with session.get(
            f"{UNIVERSALIS_BASE_URL}/worlds", timeout=aiohttp.ClientTimeout(total=15)
        ) as resp:
            resp.raise_for_status()
            payload = await resp.json()
            return {int(w["id"]): str(w["name"]) for w in payload if w.get("id") is not None}
    except Exception:
        return {}


async def fetch_aggregated(
    session: aiohttp.ClientSession, limiter: RateLimiter, ids: List[int], world: str
):
    await limiter.wait()
    ids_param = ",".join(str(i) for i in ids)
    url = f"{UNIVERSALIS_BASE_URL}/aggregated/{world}/{ids_param}"
    async with session.get(url, timeout=aiohttp.ClientTimeout(total=30)) as resp:
        resp.raise_for_status()
        return await resp.json()


def _layer(entry: Optional[dict], layer: str) -> dict:
    if not isinstance(entry, dict):
        return {}
    value = entry.get(layer)
    return value if isinstance(value, dict) else {}


def _min_listing(quality_blocks: List[dict], layer: str) -> tuple[Optional[float], Optional[int]]:
    """nq/hq 取較低的 minListing,回 (price, world_id)。"""
    best_price: Optional[float] = None
    best_world: Optional[int] = None
    for block in quality_blocks:
        info = _layer(block.get("minListing"), layer)
        price = info.get("price")
        if price is None:
            continue
        if best_price is None or price < best_price:
            best_price = float(price)
            best_world = info.get("worldId")
    return best_price, best_world


def _recent_purchase(quality_blocks: List[dict], layer: str) -> Optional[float]:
    """nq/hq 取時間較新的 recentPurchase 價格。"""
    best_ts = -1
    best_price: Optional[float] = None
    for block in quality_blocks:
        info = _layer(block.get("recentPurchase"), layer)
        price = info.get("price")
        ts = info.get("timestamp") or 0
        if price is None:
            continue
        if ts >= best_ts:
            best_ts = ts
            best_price = float(price)
    return best_price


def _daily_velocity(quality_blocks: List[dict], layer: str) -> float:
    total = 0.0
    for block in quality_blocks:
        info = _layer(block.get("dailySaleVelocity"), layer)
        qty = info.get("quantity")
        if qty:
            total += float(qty)
    return total


def _average_price(quality_blocks: List[dict], layer: str) -> Optional[float]:
    for block in quality_blocks:
        info = _layer(block.get("averageSalePrice"), layer)
        if info.get("price") is not None:
            return float(info["price"])
    return None


def build_rows_from_aggregated(
    item: dict,
    display_world: str,
    world_names: dict[int, str],
    region_label: str = LOWEST_WORLD,
) -> List[tuple]:
    """單筆 aggregated 結果 -> 兩列 prices(區域口徑 + 顯示伺服器口徑)。"""
    item_id = item.get("itemId") or item.get("itemID")
    if not item_id:
        return []

    quality_blocks = [b for b in (item.get("nq"), item.get("hq")) if isinstance(b, dict)]
    if not quality_blocks:
        return []

    upload_times = [
        normalize_timestamp(entry.get("timestamp"))
        for entry in item.get("worldUploadTimes", []) or []
        if isinstance(entry, dict)
    ]
    last_updated = max((t for t in upload_times if t), default=0)

    display_world_id = next(
        (wid for wid, name in world_names.items() if name == display_world), 0
    )

    rows: List[tuple] = []
    for scope_label, layer in ((region_label, "region"), (display_world, "world")):
        min_price, src_world_id = _min_listing(quality_blocks, layer)
        sale_price = _recent_purchase(quality_blocks, layer)
        velocity = _daily_velocity(quality_blocks, layer)
        avg_price = _average_price(quality_blocks, layer)

        if layer == "world":
            world_id = display_world_id
            world_name = display_world
        else:
            world_id = int(src_world_id) if src_world_id else 0
            world_name = world_names.get(world_id, str(world_id) if world_id else "")

        if min_price is None and sale_price is None:
            continue  # 該層無任何資料就不覆蓋舊值

        rows.append(
            (
                int(item_id),
                scope_label,
                world_id,
                world_name,
                float(avg_price) if avg_price is not None else 0,
                float(min_price) if min_price is not None else 0,
                float(sale_price) if sale_price is not None else 0,
                0,  # aggregated 不提供掛單數
                round(velocity * 3, 1),  # 近三天成交估計 = 日均成交量 * 3
                int(last_updated) if last_updated else 0,
            )
        )
    return rows


async def fetch_batch_rows(
    session: aiohttp.ClientSession,
    limiter: RateLimiter,
    ids: List[int],
    display_world: str,
    world_names: dict[int, str],
    retry_limit: int = 3,
    stats: Optional[Counter] = None,
) -> List[tuple]:
    try:
        payload = await fetch_aggregated(session, limiter, ids, display_world)
        rows: List[tuple] = []
        for item in payload.get("results", []) or []:
            rows.extend(build_rows_from_aggregated(item, display_world, world_names))
        return rows
    except aiohttp.ClientResponseError as exc:
        if stats is not None:
            stats[f"http_{exc.status}"] += 1
        if exc.status == 400:
            # 批內含不可交易道具:對切縮小範圍,單顆仍 400 就跳過該道具
            if len(ids) > 1:
                if stats is not None:
                    stats["splits"] += 1
                midpoint = len(ids) // 2
                left = await fetch_batch_rows(
                    session, limiter, ids[:midpoint], display_world, world_names, retry_limit, stats=stats
                )
                right = await fetch_batch_rows(
                    session, limiter, ids[midpoint:], display_world, world_names, retry_limit, stats=stats
                )
                return left + right
            if stats is not None:
                stats["skipped_unmarketable"] += 1
            return []
        if exc.status in {429, 500, 502, 503, 504}:
            if retry_limit > 0:
                if stats is not None:
                    stats["retries"] += 1
                await asyncio.sleep((4 - retry_limit) * 1.5 + 1)
                return await fetch_batch_rows(
                    session, limiter, ids, display_world, world_names, retry_limit - 1, stats=stats
                )
            if len(ids) > 1:
                if stats is not None:
                    stats["splits"] += 1
                midpoint = len(ids) // 2
                left = await fetch_batch_rows(
                    session, limiter, ids[:midpoint], display_world, world_names, 2, stats=stats
                )
                right = await fetch_batch_rows(
                    session, limiter, ids[midpoint:], display_world, world_names, 2, stats=stats
                )
                return left + right
        raise
    except asyncio.TimeoutError:
        if stats is not None:
            stats["timeout"] += 1
        if retry_limit > 0:
            if stats is not None:
                stats["retries"] += 1
            await asyncio.sleep((4 - retry_limit) * 1.5 + 1)
            return await fetch_batch_rows(
                session, limiter, ids, display_world, world_names, retry_limit - 1, stats=stats
            )
        raise
    except aiohttp.ClientError:
        if stats is not None:
            stats["client_error"] += 1
        if retry_limit > 0:
            if stats is not None:
                stats["retries"] += 1
            await asyncio.sleep((4 - retry_limit) * 1.5 + 1)
            return await fetch_batch_rows(
                session, limiter, ids, display_world, world_names, retry_limit - 1, stats=stats
            )
        raise


async def update_prices_async(
    ids: Optional[List[int]] = None,
    world: str = DISPLAY_WORLD,
    progress_callback: Optional[Callable[[dict], None]] = None,
    should_cancel: Optional[Callable[[], bool]] = None,
):
    """單趟更新:以 world(顯示伺服器)查 aggregated,同時落區域與該伺服器兩種口徑。"""
    display_world = world or DISPLAY_WORLD
    if display_world == LOWEST_WORLD:
        # 傳進來的是區域名(舊呼叫習慣):改用預設顯示伺服器查詢,區域層照樣會拿到
        display_world = DISPLAY_WORLD

    if not ids:
        init_db()
        with get_conn() as conn:
            ids = get_item_ids(conn)

    if not ids:
        print("No item IDs found. Import recipes first.")
        return

    scope_label = f"{LOWEST_WORLD}+{display_world}"
    batches = batch_ids(ids, MAX_BATCH_SIZE)
    limiter = RateLimiter(MAX_RPS)
    sem = asyncio.Semaphore(MAX_CONCURRENCY)
    db_lock = asyncio.Lock()
    updated_rows = 0
    completed_batches = 0
    stats: Counter = Counter()

    def report(extra: Optional[dict] = None) -> None:
        if not progress_callback:
            return
        payload = {
            "phase": "fetching_prices",
            "world": scope_label,
            "total_ids": len(ids),
            "total_batches": len(batches),
            "completed_batches": completed_batches,
            "updated_rows": updated_rows,
            "stats": dict(stats),
        }
        if extra:
            payload.update(extra)
        progress_callback(payload)

    report()

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

    # 表明身分的 User-Agent:讓 Universalis 端能辨識與統計本工具的流量(社群禮儀)
    headers = {"User-Agent": "BestMarketCrafter/1.1 (+https://github.com/zing9264/FFXIV-Best-Market-Crafter)"}
    async with aiohttp.ClientSession(headers=headers) as session:
        world_names = await fetch_world_names(session)
        marketable = await fetch_marketable_ids(session)
        if marketable:
            before = len(ids)
            ids = [i for i in ids if i in marketable]
            dropped = before - len(ids)
            if dropped:
                stats["unmarketable_filtered"] = dropped
            batches = batch_ids(ids, MAX_BATCH_SIZE)
            report()

        async def worker(batch):
            nonlocal updated_rows, completed_batches
            if should_cancel and should_cancel():
                return
            async with sem:
                if should_cancel and should_cancel():
                    return
                rows = await fetch_batch_rows(
                    session, limiter, batch, display_world, world_names, stats=stats
                )
                async with db_lock:
                    persist_rows(rows)
                    updated_rows += len(rows)
                    completed_batches += 1
                    report({"last_batch_size": len(batch)})

        tasks = [asyncio.create_task(worker(batch)) for batch in batches]
        await asyncio.gather(*tasks)

    print(f"Updated prices: {updated_rows} rows ({scope_label})")
    report()
    return updated_rows


async def update_all_prices_async(
    progress_callback: Optional[Callable[[dict], None]] = None,
    display_world: Optional[str] = None,
) -> int:
    return int(
        await update_prices_async(
            world=display_world or DISPLAY_WORLD, progress_callback=progress_callback
        )
        or 0
    )


def update_prices():
    asyncio.run(update_all_prices_async())


def update_prices_for_worlds(ids: List[int], worlds: List[str]) -> int:
    """相容舊介面:worlds 內的非區域名視為顯示伺服器,單趟同時落兩口徑。"""
    display = next((w for w in worlds if w and w != LOWEST_WORLD), DISPLAY_WORLD)
    return update_prices_for_ids(ids, world=display)


def update_prices_for_ids(ids: List[int], world: str = WORLD) -> int:
    unique_ids = sorted(set(int(item_id) for item_id in ids if int(item_id) > 0))
    if not unique_ids:
        return 0
    return int(asyncio.run(update_prices_async(unique_ids, world=world)) or 0)


if __name__ == "__main__":
    update_prices()
