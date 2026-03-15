from __future__ import annotations

import asyncio
import datetime as dt
import threading
import time

from flask import Flask, jsonify, redirect, render_template, request, url_for

from config import DB_PATH, DISPLAY_WORLD, LOWEST_WORLD
from db import get_conn, init_db
from update_prices import update_prices_async, update_prices_for_worlds
from update_profits import rebuild_profits


app = Flask(__name__)
init_db()

refresh_state_lock = threading.Lock()
refresh_thread: threading.Thread | None = None
refresh_state = {
    "running": False,
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
    return f"{value:,.2f}"


def normalize_nonzero_value(value: float | None) -> float | None:
    if value is None:
        return None
    return value if value > 0 else None


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


def start_full_refresh_job() -> bool:
    global refresh_thread
    with refresh_state_lock:
        if refresh_state["running"]:
            return False
        refresh_state.update(
            {
                "running": True,
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

    refresh_thread = threading.Thread(target=run_full_refresh_job, daemon=True)
    refresh_thread.start()
    return True


def run_full_refresh_job() -> None:
    worlds = [LOWEST_WORLD, DISPLAY_WORLD]
    total_updated = 0

    try:
        for world in worlds:
            update_refresh_state(
                phase="fetching_prices",
                message=f"正在更新 {world} 價格",
                world=world,
                total_ids=0,
                total_batches=0,
                completed_batches=0,
            )

            def on_progress(progress: dict) -> None:
                update_refresh_state(
                    phase=progress.get("phase", "fetching_prices"),
                    message=f"正在更新 {world} 價格",
                    world=world,
                    total_ids=progress.get("total_ids", 0),
                    total_batches=progress.get("total_batches", 0),
                    completed_batches=progress.get("completed_batches", 0),
                    updated_rows=total_updated + progress.get("updated_rows", 0),
                )

            updated = int(asyncio.run(update_prices_async(world=world, progress_callback=on_progress)) or 0)
            total_updated += updated
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
        update_refresh_state(
            running=False,
            phase="done",
            message="全量更新完成",
            world=DISPLAY_WORLD,
            updated_rows=total_updated,
            profits_updated=profits_updated,
            finished_at=time.time(),
        )
    except Exception as exc:
        update_refresh_state(
            running=False,
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


def get_profit_count(conn) -> int:
    cur = conn.cursor()
    cur.execute("SELECT COUNT(*) FROM profits WHERE world=?;", (DISPLAY_WORLD,))
    return int(cur.fetchone()[0] or 0)


def get_top_profit_rows(conn, limit: int = 100, offset: int = 0):
    cur = conn.cursor()
    cur.execute(
        """
        SELECT
            p.item_id,
            i.name,
            r.yield,
            p.listing_price,
            p.world_name,
            p.daily_sales,
            p.unit_material_cost,
            p.profit_by_listing
        FROM profits p
        JOIN items i ON i.item_id = p.item_id
        LEFT JOIN recipes r ON r.output_item_id = p.item_id
        WHERE p.world = ?
        ORDER BY p.profit_by_listing DESC, p.item_id ASC
        LIMIT ? OFFSET ?;
        """,
        (DISPLAY_WORLD, limit, offset),
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
                "price_gap": row[7],
            }
        )
    return rows


def load_dashboard_data():
    tab = request.args.get("tab", "lookup").strip() or "lookup"
    query = request.args.get("q", "").strip()
    selected_id = request.args.get("item_id", "").strip()
    refresh_status = request.args.get("refresh", "").strip()
    refreshed_count = request.args.get("count", "").strip()
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

        total_profit_rows = get_profit_count(conn) if tab == "ranking" else 0
        top_profits = get_top_profit_rows(conn, limit=per_page, offset=offset) if tab == "ranking" else []

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
        "refresh_state": get_refresh_state_snapshot(),
        "fmt_num": fmt_num,
        "refresh_status": refresh_status,
        "refreshed_count": refreshed_count,
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

    item_id = int(item_id_raw)
    try:
        with get_conn() as conn:
            ids = get_recipe_item_ids(conn, item_id)
        refreshed = update_prices_for_worlds(ids, [LOWEST_WORLD, DISPLAY_WORLD])
        rebuild_profits()
        return redirect(url_for("index", tab=tab, q=query, item_id=item_id, refresh="ok", count=refreshed))
    except Exception:
        return redirect(url_for("index", tab=tab, q=query, item_id=item_id, refresh="error"))


@app.post("/refresh-all-prices")
def refresh_all_prices():
    started = start_full_refresh_job()
    status = "started" if started else "running"
    return redirect(url_for("index", tab=request.form.get("tab", "lookup"), refresh=status))


@app.post("/refresh-profits")
def refresh_profits():
    page_raw = request.form.get("page", "1").strip()
    page = int(page_raw) if page_raw.isdigit() and int(page_raw) > 0 else 1
    try:
        updated = rebuild_profits()
        return redirect(url_for("index", tab="ranking", page=page, refresh="ok", count=updated))
    except Exception:
        return redirect(url_for("index", tab="ranking", page=page, refresh="error"))


@app.get("/refresh-status")
def refresh_status():
    return jsonify(get_refresh_state_snapshot())


@app.route("/health")
def health():
    return {"ok": True}


if __name__ == "__main__":
    app.run(host="127.0.0.1", port=5000, debug=True)
