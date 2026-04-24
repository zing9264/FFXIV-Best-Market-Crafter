from __future__ import annotations

import asyncio
import datetime as dt
import json
import math
import threading
import time
import traceback
from pathlib import Path

from flask import Flask, abort, jsonify, redirect, render_template, request, send_file, url_for

from config import (
    APP_DEBUG,
    APP_HOST,
    APP_LOG_PATH,
    APP_PORT,
    DB_PATH,
    DISPLAY_WORLD,
    FULL_REFRESH_COOLDOWN_SECONDS,
    LOWEST_WORLD,
    RECIPE_REFRESH_COOLDOWN_SECONDS,
    REFRESH_STATS_PATH,
)
from db import get_conn, init_db
from import_collectable_rewards import import_collectable_rewards
from materia_optimizer import (
    STAT_KEYS,
    STAT_LABELS,
    GearPiece,
    load_gear_presets,
    load_materia_stats,
    load_slot_configs,
    load_success_rates,
    optimize,
    pieces_from_preset,
)
from update_prices import update_prices_async, update_prices_for_worlds
from update_profits import rebuild_profits


app = Flask(__name__)
init_db()
import_collectable_rewards()

CRAFT_TYPE_NAMES = {
    0: "木工",
    1: "鍛鐵",
    2: "鎧甲",
    3: "雕金",
    4: "製革",
    5: "裁衣",
    6: "煉金",
    7: "烹調",
}

PRICE_SCOPE_CHOICES = {
    "all": {"world": LOWEST_WORLD, "label": "全伺服器"},
    "phoenix": {"world": DISPLAY_WORLD, "label": DISPLAY_WORLD},
}

refresh_state_lock = threading.Lock()
refresh_thread: threading.Thread | None = None
refresh_state = {
    "running": False,
    "cancel_requested": False,
    "phase": "idle",
    "message": "尚未開始全量更新",
    "world": "",
    "total_ids": 0,
    "total_batches": 0,
    "completed_batches": 0,
    "updated_rows": 0,
    "profits_updated": 0,
    "started_at": None,
    "finished_at": None,
    "error": "",
}
cooldown_lock = threading.Lock()
cooldown_state = {
    "last_recipe_refresh_at": 0.0,
    "last_full_refresh_at": 0.0,
}
refresh_log_lock = threading.Lock()


def fmt_ts(ts: int | None) -> str:
    if not ts:
        return "-"
    return dt.datetime.utcfromtimestamp(ts).strftime("%Y-%m-%d %H:%M:%S UTC")


def fmt_local_ts(ts: float | None) -> str:
    if not ts:
        return "-"
    return dt.datetime.fromtimestamp(ts).strftime("%Y-%m-%d %H:%M:%S")


def fmt_num(value: float | None) -> str:
    if value is None:
        return "-"
    return f"{math.floor(value):,}"


def fmt_pct_floor(value: float | None) -> str:
    if value is None:
        return "-"
    return f"{math.floor(value):,}%"


def fmt_daily_sales(value: float | None) -> str:
    if value is None:
        return "-"
    return f"{math.floor(value):,}"


def normalize_nonzero_value(value: float | None) -> float | None:
    if value is None:
        return None
    return value if value > 0 else None


def parse_price_scope(value: str | None) -> str:
    key = (value or "all").strip().lower()
    return key if key in PRICE_SCOPE_CHOICES else "all"


def ensure_parent_dir(path_str: str) -> Path:
    path = Path(path_str)
    if path.parent != Path("."):
        path.parent.mkdir(parents=True, exist_ok=True)
    return path


def append_app_log(message: str) -> None:
    path = ensure_parent_dir(APP_LOG_PATH)
    line = f"[{fmt_local_ts(time.time())}] {message}\n"
    with refresh_log_lock:
        with path.open("a", encoding="utf-8") as handle:
            handle.write(line)


def append_refresh_stats(event: str, **payload) -> None:
    path = ensure_parent_dir(REFRESH_STATS_PATH)
    entry = {
        "event": event,
        "logged_at": dt.datetime.now(dt.UTC).isoformat(),
        **payload,
    }
    with refresh_log_lock:
        with path.open("a", encoding="utf-8") as handle:
            handle.write(json.dumps(entry, ensure_ascii=False) + "\n")


def update_refresh_state(**changes) -> None:
    with refresh_state_lock:
        refresh_state.update(changes)


def get_refresh_state_snapshot() -> dict:
    with refresh_state_lock:
        snapshot = dict(refresh_state)
    total_batches = snapshot.get("total_batches") or 0
    completed_batches = snapshot.get("completed_batches") or 0
    snapshot["progress_pct"] = round((completed_batches / total_batches) * 100, 1) if total_batches else 0.0
    snapshot["started_at_text"] = fmt_local_ts(snapshot.get("started_at"))
    snapshot["finished_at_text"] = fmt_local_ts(snapshot.get("finished_at"))
    return snapshot


def is_cancel_requested() -> bool:
    with refresh_state_lock:
        return bool(refresh_state.get("cancel_requested"))


def get_cooldown_remaining(key: str, cooldown_seconds: int) -> int:
    with cooldown_lock:
        last = cooldown_state.get(key, 0.0)
    remaining = int(max(0, cooldown_seconds - (time.time() - last)))
    return remaining


def mark_cooldown_triggered(key: str) -> None:
    with cooldown_lock:
        cooldown_state[key] = time.time()


def start_full_refresh_job() -> bool:
    global refresh_thread
    with refresh_state_lock:
        if refresh_state["running"]:
            return False
        refresh_state.update(
            {
                "running": True,
                "cancel_requested": False,
                "phase": "fetching_prices",
                "message": "準備開始全量更新",
                "world": LOWEST_WORLD,
                "total_ids": 0,
                "total_batches": 0,
                "completed_batches": 0,
                "updated_rows": 0,
                "profits_updated": 0,
                "started_at": time.time(),
                "finished_at": None,
                "error": "",
            }
        )

    append_app_log("開始全量更新")
    append_refresh_stats(
        "refresh_started",
        worlds=[LOWEST_WORLD, DISPLAY_WORLD],
    )
    refresh_thread = threading.Thread(target=run_full_refresh_job, daemon=True)
    refresh_thread.start()
    return True


def run_full_refresh_job() -> None:
    worlds = [LOWEST_WORLD, DISPLAY_WORLD]
    total_updated = 0

    try:
        for world in worlds:
            last_logged_batch = -1
            update_refresh_state(
                phase="fetching_prices",
                message=f"正在更新 {world} 價格",
                world=world,
                total_ids=0,
                total_batches=0,
                completed_batches=0,
            )
            append_app_log(f"開始更新價格範圍：{world}")
            append_refresh_stats("world_started", world=world)

            def on_progress(progress: dict) -> None:
                nonlocal last_logged_batch
                update_refresh_state(
                    phase=progress.get("phase", "fetching_prices"),
                    message=f"正在更新 {world} 價格",
                    world=world,
                    total_ids=progress.get("total_ids", 0),
                    total_batches=progress.get("total_batches", 0),
                    completed_batches=progress.get("completed_batches", 0),
                    updated_rows=total_updated + progress.get("updated_rows", 0),
                )
                completed_batches = int(progress.get("completed_batches", 0) or 0)
                if completed_batches != last_logged_batch:
                    last_logged_batch = completed_batches
                    append_refresh_stats(
                        "world_progress",
                        world=world,
                        total_ids=int(progress.get("total_ids", 0) or 0),
                        total_batches=int(progress.get("total_batches", 0) or 0),
                        completed_batches=completed_batches,
                        updated_rows=total_updated + int(progress.get("updated_rows", 0) or 0),
                        stats=progress.get("stats", {}),
                    )

            updated = int(
                asyncio.run(
                    update_prices_async(
                        world=world,
                        progress_callback=on_progress,
                        should_cancel=is_cancel_requested,
                    )
                )
                or 0
            )
            if is_cancel_requested():
                append_app_log(f"全量更新已中斷，停止於 {world}")
                append_refresh_stats(
                    "refresh_cancelled",
                    world=world,
                    updated_rows=total_updated + updated,
                )
                update_refresh_state(
                    running=False,
                    cancel_requested=False,
                    phase="cancelled",
                    message="全量更新已中斷",
                    world=world,
                    updated_rows=total_updated + updated,
                    finished_at=time.time(),
                )
                return
            total_updated += updated
            append_app_log(f"{world} 價格更新完成，累計 {total_updated} 筆")
            append_refresh_stats(
                "world_finished",
                world=world,
                updated_rows=total_updated,
            )
            update_refresh_state(
                message=f"{world} 價格更新完成",
                world=world,
                updated_rows=total_updated,
            )

        update_refresh_state(
            phase="rebuilding_profits",
            message="價格更新完成，正在重算總價差",
            world=DISPLAY_WORLD,
            total_batches=0,
            completed_batches=0,
        )
        profits_updated = rebuild_profits()
        append_app_log(f"全量更新完成，價格 {total_updated} 筆，價差 {profits_updated} 筆")
        append_refresh_stats(
            "refresh_finished",
            updated_rows=total_updated,
            profits_updated=profits_updated,
        )
        update_refresh_state(
            running=False,
            cancel_requested=False,
            phase="done",
            message="全量更新完成",
            world=DISPLAY_WORLD,
            updated_rows=total_updated,
            profits_updated=profits_updated,
            finished_at=time.time(),
        )
    except Exception as exc:
        append_app_log(f"全量更新失敗：{exc}")
        append_refresh_stats(
            "refresh_failed",
            error=str(exc),
            traceback=traceback.format_exc(),
        )
        update_refresh_state(
            running=False,
            cancel_requested=False,
            phase="error",
            message="全量更新失敗",
            error=str(exc),
            finished_at=time.time(),
        )


def get_counts(conn):
    cur = conn.cursor()
    cur.execute("SELECT COUNT(*) FROM recipes;")
    recipes_count = cur.fetchone()[0]
    cur.execute("SELECT COUNT(*) FROM recipe_ingredients;")
    ingredients_count = cur.fetchone()[0]
    cur.execute("SELECT COUNT(*) FROM prices WHERE world=?;", (LOWEST_WORLD,))
    lowest_prices_count = cur.fetchone()[0]
    cur.execute("SELECT COUNT(*) FROM prices WHERE world=?;", (DISPLAY_WORLD,))
    display_prices_count = cur.fetchone()[0]
    return {
        "recipes": recipes_count,
        "ingredients": ingredients_count,
        "prices_lowest": lowest_prices_count,
        "prices_display": display_prices_count,
    }


def get_latest_prices(conn):
    cur = conn.cursor()
    cur.execute(
        """
        SELECT
            p.item_id,
            i.name,
            p.world_name,
            p.min_price,
            p.sale_price,
            p.listings,
            p.daily_sales,
            p.last_updated
        FROM prices p
        LEFT JOIN items i ON i.item_id = p.item_id
        WHERE p.world=?
        ORDER BY p.last_updated DESC NULLS LAST
        LIMIT 50;
        """,
        (LOWEST_WORLD,),
    )
    return [
        {
            "item_id": row[0],
            "name": row[1] or f"Item {row[0]}",
            "world_name": row[2] or "-",
            "min_price": row[3],
            "sale_price": normalize_nonzero_value(row[4]),
            "listings": row[5],
            "daily_sales": normalize_nonzero_value(row[6]),
            "last_updated": fmt_ts(row[7]),
        }
        for row in cur.fetchall()
    ]


def get_recipe_samples(conn):
    cur = conn.cursor()
    cur.execute(
        """
        SELECT r.output_item_id, i.name, r.yield, COUNT(ri.ingredient_item_id) AS ing_count
        FROM recipes r
        LEFT JOIN items i ON i.item_id = r.output_item_id
        LEFT JOIN recipe_ingredients ri ON ri.output_item_id = r.output_item_id
        GROUP BY r.output_item_id
        ORDER BY ing_count DESC, r.output_item_id ASC
        LIMIT 30;
        """
    )
    recipes = []
    for row in cur.fetchall():
        output_id = row[0]
        cur.execute(
            """
            SELECT ri.ingredient_item_id, i.name, ri.qty
            FROM recipe_ingredients ri
            LEFT JOIN items i ON i.item_id = ri.ingredient_item_id
            WHERE ri.output_item_id=?
            ORDER BY ri.qty DESC, ri.ingredient_item_id ASC
            LIMIT 5;
            """,
            (output_id,),
        )
        ingredients = [
            {
                "item_id": r2[0],
                "name": r2[1] or f"Item {r2[0]}",
                "qty": r2[2],
            }
            for r2 in cur.fetchall()
        ]
        recipes.append(
            {
                "item_id": output_id,
                "name": row[1] or f"Item {output_id}",
                "yield": row[2],
                "ing_count": row[3],
                "ingredients": ingredients,
            }
        )
    return recipes


def search_items(conn, query: str):
    if not query:
        return []

    cur = conn.cursor()
    text = query.strip()
    if text.isdigit():
        cur.execute(
            """
            SELECT i.item_id, i.name, r.output_item_id IS NOT NULL AS has_recipe
            FROM items i
            LEFT JOIN recipes r ON r.output_item_id = i.item_id
            WHERE i.item_id = ?
            ORDER BY has_recipe DESC, i.item_id ASC
            LIMIT 20;
            """,
            (int(text),),
        )
    else:
        cur.execute(
            """
            SELECT i.item_id, i.name, r.output_item_id IS NOT NULL AS has_recipe
            FROM items i
            LEFT JOIN recipes r ON r.output_item_id = i.item_id
            WHERE i.name LIKE ?
            ORDER BY has_recipe DESC, i.name ASC, i.item_id ASC
            LIMIT 20;
            """,
            (f"%{text}%",),
        )

    return [
        {
            "item_id": row[0],
            "name": row[1] or f"Item {row[0]}",
            "has_recipe": bool(row[2]),
        }
        for row in cur.fetchall()
    ]


def load_recipe_detail(conn, item_id: int):
    cur = conn.cursor()
    cur.execute(
        """
        SELECT
            i.item_id,
            i.name,
            r.yield,
            dp.world_name,
            dp.min_price,
            dp.sale_price,
            dp.listings,
            dp.daily_sales,
            dp.last_updated
        FROM items i
        LEFT JOIN recipes r ON r.output_item_id = i.item_id
        LEFT JOIN prices dp ON dp.item_id = i.item_id AND dp.world = ?
        WHERE i.item_id = ?;
        """,
        (DISPLAY_WORLD, item_id),
    )
    row = cur.fetchone()
    if not row:
        return None

    has_recipe = row[2] is not None
    yield_qty = row[2] or 1
    product_sell_price = normalize_nonzero_value(row[4])
    product_sale_price = normalize_nonzero_value(row[5])

    cur.execute(
        """
        SELECT
            ri.ingredient_item_id,
            i.name,
            ri.qty,
            fp.world_name,
            fp.min_price,
            dp.min_price,
            dp.world_name,
            fp.listings,
            fp.daily_sales,
            fp.last_updated
        FROM recipe_ingredients ri
        LEFT JOIN items i ON i.item_id = ri.ingredient_item_id
        LEFT JOIN prices fp ON fp.item_id = ri.ingredient_item_id AND fp.world = ?
        LEFT JOIN prices dp ON dp.item_id = ri.ingredient_item_id AND dp.world = ?
        WHERE ri.output_item_id = ?
        ORDER BY ri.qty DESC, ri.ingredient_item_id ASC;
        """,
        (LOWEST_WORLD, DISPLAY_WORLD, item_id),
    )

    ingredients = []
    materials_total = 0.0
    has_all_prices = True
    for ing in cur.fetchall():
        unit_price = normalize_nonzero_value(ing[4])
        line_total = unit_price * ing[2] if unit_price is not None else None
        if line_total is None:
            has_all_prices = False
        else:
            materials_total += line_total
        ingredients.append(
            {
                "item_id": ing[0],
                "name": ing[1] or f"Item {ing[0]}",
                "qty": ing[2],
                "lowest_world_name": ing[3] or "-",
                "lowest_price": normalize_nonzero_value(ing[4]),
                "display_price": normalize_nonzero_value(ing[5]),
                "display_world_name": ing[6] or DISPLAY_WORLD,
                "unit_price": unit_price,
                "line_total": line_total,
                "listings": ing[7],
                "daily_sales": normalize_nonzero_value(ing[8]),
                "last_updated": fmt_ts(ing[9]),
            }
        )

    if not ingredients:
        has_all_prices = False
        materials_total = None

    unit_material_cost = None
    if has_all_prices and materials_total is not None and yield_qty > 0:
        unit_material_cost = materials_total / yield_qty

    price_gap = None
    if product_sell_price is not None and unit_material_cost is not None:
        price_gap = product_sell_price - unit_material_cost

    return {
        "item_id": row[0],
        "name": row[1] or f"Item {row[0]}",
        "yield": yield_qty,
        "has_recipe": has_recipe,
        "product": {
            "world_name": row[3] or DISPLAY_WORLD,
            "min_price": row[4],
            "sale_price": product_sale_price,
            "sell_price": product_sell_price,
            "listings": row[6],
            "daily_sales": normalize_nonzero_value(row[7]),
            "last_updated": fmt_ts(row[8]),
        },
        "ingredients": ingredients,
        "materials_total": materials_total if has_all_prices else None,
        "unit_material_cost": unit_material_cost,
        "price_gap": price_gap,
        "display_world": DISPLAY_WORLD,
        "lowest_world": LOWEST_WORLD,
    }


def get_recipe_item_ids(conn, item_id: int) -> list[int]:
    cur = conn.cursor()
    cur.execute("SELECT ingredient_item_id FROM recipe_ingredients WHERE output_item_id=?;", (item_id,))
    ids = [item_id]
    ids.extend(row[0] for row in cur.fetchall())
    return sorted(set(int(value) for value in ids if int(value) > 0))


COLLECTABLE_SCRIP_TYPES = {
    "purple": {
        "column": "purple_scrips",
        "label": "紫票",
        "cost_label": "每張紫票成本",
    },
    "orange": {
        "column": "orange_scrips",
        "label": "橘票",
        "cost_label": "每張橘票成本",
    },
}


def normalize_scrip_type(value: str) -> str:
    """Clamp an arbitrary string to a supported scrip type, defaulting to purple."""
    value = (value or "").strip().lower()
    return value if value in COLLECTABLE_SCRIP_TYPES else "purple"


def get_collectable_rows(
    conn,
    pricing_world: str,
    sort_dir: str = "desc",
    scrip_type: str = "purple",
):
    order = "DESC" if sort_dir == "desc" else "ASC"
    scrip_type = normalize_scrip_type(scrip_type)
    # `column` is whitelisted via COLLECTABLE_SCRIP_TYPES, so direct
    # interpolation into the SQL below is safe from injection.
    column = COLLECTABLE_SCRIP_TYPES[scrip_type]["column"]
    cur = conn.cursor()
    cur.execute(
        f"""
        SELECT
            cr.item_id,
            i.name,
            cr.craft_type,
            cr.class_job_level,
            cr.{column},
            SUM(ri.qty * fp.min_price) / r.yield AS unit_material_cost,
            CASE
                WHEN COUNT(pp.item_id) = COUNT(*)
                THEN SUM(ri.qty * pp.min_price) / r.yield
                ELSE 0
            END AS display_unit_material_cost,
            (SUM(ri.qty * fp.min_price) / r.yield) / cr.{column} AS cost_per_scrip
        FROM collectable_rewards cr
        JOIN items i ON i.item_id = cr.item_id
        JOIN recipes r ON r.output_item_id = cr.item_id
        JOIN recipe_ingredients ri ON ri.output_item_id = cr.item_id
        JOIN prices fp
          ON fp.item_id = ri.ingredient_item_id
         AND fp.world = ?
         AND fp.min_price > 0
        LEFT JOIN prices pp
          ON pp.item_id = ri.ingredient_item_id
         AND pp.world = ?
         AND pp.min_price > 0
        WHERE cr.{column} > 0
        GROUP BY
            cr.item_id,
            i.name,
            cr.craft_type,
            cr.class_job_level,
            cr.{column},
            r.yield
        HAVING COUNT(*) = (
            SELECT COUNT(*)
            FROM recipe_ingredients ri2
            WHERE ri2.output_item_id = cr.item_id
        )
        ORDER BY cost_per_scrip {order}, cr.item_id ASC;
        """,
        (pricing_world, DISPLAY_WORLD),
    )
    rows = []
    for row in cur.fetchall():
        rows.append(
            {
                "item_id": row[0],
                "name": row[1] or f"Item {row[0]}",
                "craft_type": row[2],
                "craft_type_name": CRAFT_TYPE_NAMES.get(row[2], f"職業{row[2]}"),
                "class_job_level": row[3],
                "scrip_amount": row[4],
                # Preserve the historical key so existing consumers/tests that
                # read `purple_scrips` on a purple query keep working.
                "purple_scrips": row[4] if scrip_type == "purple" else 0,
                "orange_scrips": row[4] if scrip_type == "orange" else 0,
                "unit_material_cost": row[5],
                "display_unit_material_cost": normalize_nonzero_value(row[6]),
                "cost_per_scrip": row[7],
            }
        )
    return rows


def get_materia_prices(conn, world: str) -> dict[int, float]:
    """Return a {materia_item_id: min_price} map for the given world.

    Only materia that (a) appear in data/materia_stats.csv AND (b) have a
    positive min_price in the prices table are returned. The optimizer won't
    recommend materia we can't price.
    """
    materia = load_materia_stats()
    if not materia:
        return {}
    ids = [m.item_id for m in materia]
    placeholders = ",".join(["?"] * len(ids))
    cur = conn.cursor()
    cur.execute(
        f"""
        SELECT item_id, min_price
        FROM prices
        WHERE item_id IN ({placeholders})
          AND world = ?
          AND min_price > 0;
        """,
        [*ids, world],
    )
    return {row[0]: float(row[1]) for row in cur.fetchall()}


def parse_nonnegative_float(value: str, default: float = 0.0) -> float:
    try:
        parsed = float(value)
    except (TypeError, ValueError):
        return default
    return parsed if parsed >= 0 else default


def get_profit_count(
    conn,
    profit_world: str,
    min_daily_sales: float = 0.0,
    min_price_gap: float = 0.0,
    min_past_profit: float = 0.0,
    min_margin_pct: float = 0.0,
    min_past_margin_pct: float = 0.0,
    min_listing_price: float = 0.0,
) -> int:
    cur = conn.cursor()
    cur.execute(
        """
        SELECT COUNT(*)
        FROM profits
        WHERE world=?
          AND daily_sales >= ?
          AND profit_by_listing >= ?
          AND profit_by_sale >= ?
          AND profit_margin_pct >= ?
          AND sale_margin_pct >= ?
          AND listing_price >= ?;
        """,
        (
            profit_world,
            min_daily_sales,
            min_price_gap,
            min_past_profit,
            min_margin_pct,
            min_past_margin_pct,
            min_listing_price,
        ),
    )
    return int(cur.fetchone()[0] or 0)


def get_top_profit_rows(
    conn,
    profit_world: str,
    limit: int = 100,
    offset: int = 0,
    sort_by: str = "profit",
    min_daily_sales: float = 0.0,
    min_price_gap: float = 0.0,
    min_past_profit: float = 0.0,
    min_margin_pct: float = 0.0,
    min_past_margin_pct: float = 0.0,
    min_listing_price: float = 0.0,
):
    order_column = {
        "profit": "p.profit_by_listing",
        "margin": "p.profit_margin_pct",
        "past_profit": "p.profit_by_sale",
        "past_margin": "p.sale_margin_pct",
    }.get(sort_by, "p.profit_by_listing")
    cur = conn.cursor()
    cur.execute(
        f"""
        SELECT
            p.item_id,
            i.name,
            r.yield,
            p.listing_price,
            p.world_name,
            p.daily_sales,
            p.unit_material_cost,
            p.display_unit_material_cost,
            p.profit_by_listing,
            p.profit_margin_pct,
            p.profit_by_sale,
            p.sale_margin_pct
        FROM profits p
        JOIN items i ON i.item_id = p.item_id
        LEFT JOIN recipes r ON r.output_item_id = p.item_id
        WHERE p.world = ?
          AND p.daily_sales >= ?
          AND p.profit_by_listing >= ?
          AND p.profit_by_sale >= ?
          AND p.profit_margin_pct >= ?
          AND p.sale_margin_pct >= ?
          AND p.listing_price >= ?
        ORDER BY {order_column} DESC, p.profit_by_listing DESC, p.profit_by_sale DESC, p.item_id ASC
        LIMIT ? OFFSET ?;
        """,
        (
            profit_world,
            min_daily_sales,
            min_price_gap,
            min_past_profit,
            min_margin_pct,
            min_past_margin_pct,
            min_listing_price,
            limit,
            offset,
        ),
    )
    rows = []
    for row in cur.fetchall():
        rows.append(
            {
                "item_id": row[0],
                "name": row[1] or f"Item {row[0]}",
                "yield": row[2] or 1,
                "sell_price": normalize_nonzero_value(row[3]),
                "world_name": row[4] or DISPLAY_WORLD,
                "daily_sales": normalize_nonzero_value(row[5]),
                "unit_material_cost": row[6],
                "display_unit_material_cost": normalize_nonzero_value(row[7]),
                "price_gap": row[8],
                "profit_margin_pct": row[9],
                "past_profit": row[10],
                "past_margin_pct": row[11],
            }
        )
    return rows


def load_dashboard_data():
    tab = request.args.get("tab", "lookup").strip() or "lookup"
    query = request.args.get("q", "").strip()
    selected_id = request.args.get("item_id", "").strip()
    refresh_status = request.args.get("refresh", "").strip()
    refreshed_count = request.args.get("count", "").strip()
    cooldown_remaining = request.args.get("cooldown_remaining", "").strip()
    ranking_sort = request.args.get("sort", "profit").strip()
    if ranking_sort not in {"profit", "margin", "past_profit", "past_margin"}:
        ranking_sort = "profit"
    min_daily_sales = parse_nonnegative_float(request.args.get("min_daily_sales", "0"), default=0.0)
    min_price_gap = parse_nonnegative_float(request.args.get("min_price_gap", "0"), default=0.0)
    min_past_profit = parse_nonnegative_float(request.args.get("min_past_profit", "0"), default=0.0)
    min_margin_pct = parse_nonnegative_float(request.args.get("min_margin_pct", "0"), default=0.0)
    min_past_margin_pct = parse_nonnegative_float(request.args.get("min_past_margin_pct", "0"), default=0.0)
    min_listing_price = parse_nonnegative_float(request.args.get("min_listing_price", "0"), default=0.0)
    price_scope = parse_price_scope(request.args.get("price_scope", "all"))
    price_scope_world = PRICE_SCOPE_CHOICES[price_scope]["world"]
    price_scope_label = PRICE_SCOPE_CHOICES[price_scope]["label"]
    collectable_sort = request.args.get("collectable_sort", "desc").strip()
    if collectable_sort not in {"asc", "desc"}:
        collectable_sort = "desc"
    collectable_scrip_type = normalize_scrip_type(request.args.get("scrip_type", "purple"))

    # ---- Materia optimizer inputs (only consumed when tab == "materia") ----
    materia_preset_key = request.args.get("materia_preset", "crp_745_explorer").strip()
    materia_target = {
        "craftsmanship": int(request.args.get("target_craft", "5386") or 0),
        "control": int(request.args.get("target_ctrl", "5246") or 0),
        "cp": int(request.args.get("target_cp", "628") or 0),
    }
    materia_locked_pieces: dict[str, dict[str, int]] = {}
    for piece_key in (
        "main_hand", "off_hand", "head", "body", "hands", "legs", "feet",
        "earrings", "necklace", "bracelet", "ring_1", "ring_2",
    ):
        if request.args.get(f"lock_{piece_key}"):
            materia_locked_pieces[piece_key] = {
                "craftsmanship": int(request.args.get(f"lock_{piece_key}_craft", "0") or 0),
                "control": int(request.args.get(f"lock_{piece_key}_ctrl", "0") or 0),
                "cp": int(request.args.get(f"lock_{piece_key}_cp", "0") or 0),
            }
    materia_run = request.args.get("materia_run", "").strip() == "1"
    # Hard-cap top_k: each extra iteration is a full CBC re-solve with a
    # fresh no-good cut and peaks memory further. K=10 was observed to OOM
    # / hang on a 16 GB machine, so clamp to 5 and default to 3.
    materia_top_k = max(1, min(5, int(request.args.get("materia_top_k", "3") or 3)))
    page_raw = request.args.get("page", "1").strip()
    page = int(page_raw) if page_raw.isdigit() and int(page_raw) > 0 else 1
    per_page = 100
    offset = (page - 1) * per_page

    with get_conn() as conn:
        counts = get_counts(conn)
        prices = get_latest_prices(conn)
        recipes = get_recipe_samples(conn)
        search_results = search_items(conn, query)

        detail = None
        if selected_id.isdigit():
            detail = load_recipe_detail(conn, int(selected_id))
        elif len(search_results) == 1:
            detail = load_recipe_detail(conn, search_results[0]["item_id"])

        total_profit_rows = (
            get_profit_count(
                conn,
                profit_world=price_scope_world,
                min_daily_sales=min_daily_sales,
                min_price_gap=min_price_gap,
                min_past_profit=min_past_profit,
                min_margin_pct=min_margin_pct,
                min_past_margin_pct=min_past_margin_pct,
                min_listing_price=min_listing_price,
            )
            if tab == "ranking"
            else 0
        )
        top_profits = (
            get_top_profit_rows(
                conn,
                profit_world=price_scope_world,
                limit=per_page,
                offset=offset,
                sort_by=ranking_sort,
                min_daily_sales=min_daily_sales,
                min_price_gap=min_price_gap,
                min_past_profit=min_past_profit,
                min_margin_pct=min_margin_pct,
                min_past_margin_pct=min_past_margin_pct,
                min_listing_price=min_listing_price,
            )
            if tab == "ranking"
            else []
        )
        collectables = (
            get_collectable_rows(
                conn,
                pricing_world=price_scope_world,
                sort_dir=collectable_sort,
                scrip_type=collectable_scrip_type,
            )
            if tab == "collectables"
            else []
        )

        # Materia optimizer tab — only run the solver when the user explicitly
        # submits the form (materia_run=1). Prices come from the DB.
        materia_solutions_payload = {"solutions": []}
        materia_presets = load_gear_presets()
        materia_preset = materia_presets.get(
            materia_preset_key, next(iter(materia_presets.values()))
        )
        materia_slot_configs = load_slot_configs()

        # Payload for the interactive manual-meld builder (client-side JS).
        materia_interactive_payload = None
        if tab == "materia":
            all_materia = load_materia_stats()
            prices_for_interactive = get_materia_prices(conn, DISPLAY_WORLD)
            materia_interactive_payload = {
                "preset_key": materia_preset_key,
                "targets": materia_target,
                "base_stats": materia_preset["base_stats_total"],
                "pieces": [
                    {
                        "index": idx,
                        "slot_key": p["slot_key"],
                        "label": p["label"],
                        "base": p["base"],
                        "cap": p["cap"],
                        "headroom": p["headroom"],
                        "slot_config": {
                            "safe_sockets": materia_slot_configs[p["slot_key"]].safe_sockets,
                            "total_sockets": materia_slot_configs[p["slot_key"]].total_sockets,
                        },
                    }
                    for idx, p in enumerate(materia_preset["pieces"])
                ],
                "materia": [
                    {
                        "item_id": m.item_id,
                        "name": m.name,
                        "series": m.series,
                        "tier": m.tier,
                        "stat_type": m.stat_type,
                        "stat_value": m.stat_value,
                        "price": prices_for_interactive.get(m.item_id, 0),
                    }
                    for m in all_materia
                ],
                "success_rates": load_success_rates(),
                "current_max_tier": 12,
            }
        materia_results: list = []
        materia_error: str | None = None
        materia_solve_seconds: float = 0.0
        if tab == "materia" and materia_run:
            from time import perf_counter

            prices_map = get_materia_prices(conn, DISPLAY_WORLD)
            if not prices_map:
                materia_error = (
                    "找不到魔晶石的價格資料,請先跑價格更新(全量更新或指定 ID)。"
                )
            else:
                # Build GearPiece objects from the preset, overlaying any
                # locked-piece inputs coming from the form.
                piece_objs = []
                for piece_info in materia_preset["pieces"]:
                    locked = piece_info["slot_key"] in materia_locked_pieces
                    piece_objs.append(
                        GearPiece(
                            slot_key=piece_info["slot_key"],
                            label=piece_info["label"],
                            headroom=dict(piece_info["headroom"]),
                            locked=locked,
                            locked_contribution=(
                                materia_locked_pieces[piece_info["slot_key"]]
                                if locked
                                else {k: 0 for k in STAT_KEYS}
                            ),
                        )
                    )
                try:
                    t0 = perf_counter()
                    materia_results = optimize(
                        targets=materia_target,
                        base_stats=materia_preset["base_stats_total"],
                        pieces=piece_objs,
                        prices=prices_map,
                        top_k=materia_top_k,
                        solver_timeout_seconds=12,
                    )
                    materia_solve_seconds = perf_counter() - t0
                except ValueError as exc:
                    materia_error = str(exc)

        # Build a JSON-serialisable bundle of ILP solutions for the
        # client-side "apply solution" buttons. Done here in Python so the
        # template doesn't have to wrestle with nested comprehensions.
        materia_solutions_payload = {
            "solutions": [
                {
                    "assignments": [
                        {
                            "piece_index": a["piece_index"],
                            "socket_index": a["socket_index"],
                            "materia_id": (
                                a["materia"]["item_id"] if a.get("materia") else None
                            ),
                            "locked": a.get("locked", False),
                        }
                        for a in r.assignments
                    ]
                }
                for r in materia_results
            ]
        }

    total_pages = max(1, (total_profit_rows + per_page - 1) // per_page) if tab == "ranking" else 1

    return {
        "display_world": DISPLAY_WORLD,
        "lowest_world": LOWEST_WORLD,
        "db_path": DB_PATH,
        "counts": counts,
        "prices": prices,
        "recipes": recipes,
        "tab": tab,
        "query": query,
        "search_results": search_results,
        "detail": detail,
        "top_profits": top_profits,
        "ranking_page": page,
        "ranking_per_page": per_page,
        "ranking_total": total_profit_rows,
        "ranking_total_pages": total_pages,
        "ranking_sort": ranking_sort,
        "price_scope": price_scope,
        "price_scope_label": price_scope_label,
        "price_scope_choices": PRICE_SCOPE_CHOICES,
        "collectables": collectables,
        "collectable_sort": collectable_sort,
        "collectable_scrip_type": collectable_scrip_type,
        "collectable_scrip_label": COLLECTABLE_SCRIP_TYPES[collectable_scrip_type]["label"],
        "collectable_cost_label": COLLECTABLE_SCRIP_TYPES[collectable_scrip_type]["cost_label"],
        # Materia optimizer
        "materia_presets": materia_presets,
        "materia_preset": materia_preset,
        "materia_preset_key": materia_preset_key,
        "materia_target": materia_target,
        "materia_locked_pieces": materia_locked_pieces,
        "materia_top_k": materia_top_k,
        "materia_run": materia_run,
        "materia_slot_configs": materia_slot_configs,
        "materia_results": materia_results,
        "materia_error": materia_error,
        "materia_solve_seconds": materia_solve_seconds,
        "materia_stat_labels": STAT_LABELS,
        "materia_interactive_payload": materia_interactive_payload,
        "materia_solutions_payload": materia_solutions_payload if tab == "materia" else {"solutions": []},
        "min_daily_sales": min_daily_sales,
        "min_price_gap": min_price_gap,
        "min_past_profit": min_past_profit,
        "min_margin_pct": min_margin_pct,
        "min_past_margin_pct": min_past_margin_pct,
        "min_listing_price": min_listing_price,
        "refresh_state": get_refresh_state_snapshot(),
        "fmt_num": fmt_num,
        "fmt_pct_floor": fmt_pct_floor,
        "fmt_daily_sales": fmt_daily_sales,
        "refresh_status": refresh_status,
        "refreshed_count": refreshed_count,
        "cooldown_remaining": cooldown_remaining,
    }


@app.route("/")
def index():
    return render_template("index.html", **load_dashboard_data())


@app.post("/refresh-recipe-prices")
def refresh_recipe_prices():
    item_id_raw = request.form.get("item_id", "").strip()
    query = request.form.get("q", "").strip()
    tab = request.form.get("tab", "lookup").strip() or "lookup"
    if not item_id_raw.isdigit():
        return redirect(url_for("index", tab=tab, q=query, refresh="invalid"))
    remaining = get_cooldown_remaining("last_recipe_refresh_at", RECIPE_REFRESH_COOLDOWN_SECONDS)
    if remaining > 0:
        return redirect(
            url_for(
                "index",
                tab=tab,
                q=query,
                item_id=item_id_raw,
                refresh="cooldown_recipe",
                cooldown_remaining=remaining,
            )
        )

    item_id = int(item_id_raw)
    try:
        with get_conn() as conn:
            ids = get_recipe_item_ids(conn, item_id)
        mark_cooldown_triggered("last_recipe_refresh_at")
        refreshed = update_prices_for_worlds(ids, [LOWEST_WORLD, DISPLAY_WORLD])
        rebuild_profits()
        return redirect(url_for("index", tab=tab, q=query, item_id=item_id, refresh="ok", count=refreshed))
    except Exception:
        return redirect(url_for("index", tab=tab, q=query, item_id=item_id, refresh="error"))


@app.post("/refresh-all-prices")
def refresh_all_prices():
    remaining = get_cooldown_remaining("last_full_refresh_at", FULL_REFRESH_COOLDOWN_SECONDS)
    if remaining > 0:
        return redirect(
            url_for(
                "index",
                tab=request.form.get("tab", "lookup"),
                refresh="cooldown_full",
                cooldown_remaining=remaining,
            )
        )
    started = start_full_refresh_job()
    if started:
        mark_cooldown_triggered("last_full_refresh_at")
    status = "started" if started else "running"
    return redirect(url_for("index", tab=request.form.get("tab", "lookup"), refresh=status))


@app.post("/cancel-refresh")
def cancel_refresh():
    tab = request.form.get("tab", "lookup").strip() or "lookup"
    with refresh_state_lock:
        if refresh_state["running"]:
            refresh_state["cancel_requested"] = True
            refresh_state["message"] = "已收到中斷請求，等待目前 batch 結束"
            append_app_log("收到中斷全量更新請求")
            append_refresh_stats("cancel_requested", world=refresh_state.get("world", ""))
            return redirect(url_for("index", tab=tab, refresh="cancel_requested"))
    return redirect(url_for("index", tab=tab, refresh="not_running"))


@app.post("/refresh-profits")
def refresh_profits():
    page_raw = request.form.get("page", "1").strip()
    page = int(page_raw) if page_raw.isdigit() and int(page_raw) > 0 else 1
    ranking_sort = request.form.get("sort", "profit").strip()
    if ranking_sort not in {"profit", "margin", "past_profit", "past_margin"}:
        ranking_sort = "profit"
    min_daily_sales = parse_nonnegative_float(request.form.get("min_daily_sales", "0"), default=0.0)
    min_price_gap = parse_nonnegative_float(request.form.get("min_price_gap", "0"), default=0.0)
    min_past_profit = parse_nonnegative_float(request.form.get("min_past_profit", "0"), default=0.0)
    min_margin_pct = parse_nonnegative_float(request.form.get("min_margin_pct", "0"), default=0.0)
    min_past_margin_pct = parse_nonnegative_float(request.form.get("min_past_margin_pct", "0"), default=0.0)
    min_listing_price = parse_nonnegative_float(request.form.get("min_listing_price", "0"), default=0.0)
    price_scope = parse_price_scope(request.form.get("price_scope", "all"))
    try:
        updated = rebuild_profits()
        return redirect(
            url_for(
                "index",
                tab="ranking",
                page=page,
                sort=ranking_sort,
                price_scope=price_scope,
                min_daily_sales=min_daily_sales,
                min_price_gap=min_price_gap,
                min_past_profit=min_past_profit,
                min_margin_pct=min_margin_pct,
                min_past_margin_pct=min_past_margin_pct,
                min_listing_price=min_listing_price,
                refresh="ok",
                count=updated,
            )
        )
    except Exception:
        return redirect(
            url_for(
                "index",
                tab="ranking",
                page=page,
                sort=ranking_sort,
                price_scope=price_scope,
                min_daily_sales=min_daily_sales,
                min_price_gap=min_price_gap,
                min_past_profit=min_past_profit,
                min_margin_pct=min_margin_pct,
                min_past_margin_pct=min_past_margin_pct,
                min_listing_price=min_listing_price,
                refresh="error",
            )
        )


@app.get("/refresh-status")
def refresh_status():
    return jsonify(get_refresh_state_snapshot())


@app.get("/logs/app")
def download_app_log():
    path = Path(APP_LOG_PATH)
    if not path.exists():
        abort(404)
    return send_file(path, as_attachment=True, download_name=path.name, mimetype="text/plain")


@app.get("/logs/refresh-stats")
def download_refresh_stats():
    path = Path(REFRESH_STATS_PATH)
    if not path.exists():
        abort(404)
    return send_file(path, as_attachment=True, download_name=path.name, mimetype="application/jsonl")


@app.route("/health")
def health():
    return {"ok": True}


if __name__ == "__main__":
    app.run(host=APP_HOST, port=APP_PORT, debug=APP_DEBUG)
