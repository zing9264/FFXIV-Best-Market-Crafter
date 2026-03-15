from __future__ import annotations

import datetime as dt

from flask import Flask, redirect, render_template, request, url_for

from config import BUY_PRICE_FIELD, DB_PATH, SELL_PRICE_FIELD, WORLD
from db import get_conn, init_db
from update_prices import update_prices_for_ids


app = Flask(__name__)


def fmt_ts(ts: int | None) -> str:
    if not ts:
        return "-"
    return dt.datetime.utcfromtimestamp(ts).strftime("%Y-%m-%d %H:%M:%S UTC")


def fmt_num(value: float | None) -> str:
    if value is None:
        return "-"
    return f"{value:,.2f}"


def normalize_price_value(value: float | None) -> float | None:
    if value is None:
        return None
    return value if value > 0 else None


def normalize_positive_value(value: float | None) -> float | None:
    if value is None:
        return None
    return value if value > 0 else None


def choose_price(p50_price: float | None, min_price: float | None, field: str) -> float | None:
    p50_price = normalize_price_value(p50_price)
    min_price = normalize_price_value(min_price)
    if field == "min":
        return min_price if min_price is not None else p50_price
    return p50_price if p50_price is not None else min_price


def get_counts(conn):
    cur = conn.cursor()
    cur.execute("SELECT COUNT(*) FROM items;")
    items_count = cur.fetchone()[0]
    cur.execute("SELECT COUNT(*) FROM recipes;")
    recipes_count = cur.fetchone()[0]
    cur.execute("SELECT COUNT(*) FROM recipe_ingredients;")
    ingredients_count = cur.fetchone()[0]
    cur.execute("SELECT COUNT(*) FROM prices WHERE world=?;", (WORLD,))
    prices_count = cur.fetchone()[0]
    return {
        "items": items_count,
        "recipes": recipes_count,
        "ingredients": ingredients_count,
        "prices": prices_count,
    }


def get_latest_prices(conn):
    cur = conn.cursor()
    cur.execute(
        """
        SELECT
            p.item_id,
            i.name,
            p.world_name,
            p.p50_price,
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
        (WORLD,),
    )
    return [
        {
            "item_id": row[0],
            "name": row[1] or f"Item {row[0]}",
            "world_name": row[2] or "-",
            "p50_price": row[3],
            "min_price": row[4],
            "sale_price": normalize_price_value(row[5]),
            "listings": row[6],
            "daily_sales": normalize_positive_value(row[7]),
            "last_updated": fmt_ts(row[8]),
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
            p.world_name,
            p.p50_price,
            p.min_price,
            p.sale_price,
            p.listings,
            p.daily_sales,
            p.last_updated
        FROM items i
        LEFT JOIN recipes r ON r.output_item_id = i.item_id
        LEFT JOIN prices p ON p.item_id = i.item_id AND p.world = ?
        WHERE i.item_id = ?;
        """,
        (WORLD, item_id),
    )
    row = cur.fetchone()
    if not row:
        return None

    has_recipe = row[2] is not None
    yield_qty = row[2] or 1
    product_sell_price = choose_price(row[4], row[5], SELL_PRICE_FIELD)
    product_sale_price = normalize_price_value(row[6])

    cur.execute(
        """
        SELECT
            ri.ingredient_item_id,
            i.name,
            ri.qty,
            p.p50_price,
            p.min_price,
            p.listings,
            p.daily_sales,
            p.last_updated
        FROM recipe_ingredients ri
        LEFT JOIN items i ON i.item_id = ri.ingredient_item_id
        LEFT JOIN prices p ON p.item_id = ri.ingredient_item_id AND p.world = ?
        WHERE ri.output_item_id = ?
        ORDER BY ri.qty DESC, ri.ingredient_item_id ASC;
        """,
        (WORLD, item_id),
    )

    ingredients = []
    materials_total = 0.0
    has_all_prices = True
    for ing in cur.fetchall():
        unit_price = choose_price(ing[3], ing[4], BUY_PRICE_FIELD)
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
                "p50_price": ing[3],
                "min_price": ing[4],
                "unit_price": unit_price,
                "line_total": line_total,
                "listings": ing[5],
                "daily_sales": normalize_positive_value(ing[6]),
                "last_updated": fmt_ts(ing[7]),
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
            "p50_price": row[3],
            "world_name": row[3] or "-",
            "p50_price": row[4],
            "min_price": row[5],
            "sale_price": product_sale_price,
            "sell_price": product_sell_price,
            "listings": row[7],
            "daily_sales": normalize_positive_value(row[8]),
            "last_updated": fmt_ts(row[9]),
        },
        "ingredients": ingredients,
        "materials_total": materials_total if has_all_prices else None,
        "unit_material_cost": unit_material_cost,
        "price_gap": price_gap,
    }


def get_recipe_item_ids(conn, item_id: int) -> list[int]:
    cur = conn.cursor()
    cur.execute("SELECT ingredient_item_id FROM recipe_ingredients WHERE output_item_id=?;", (item_id,))
    ids = [item_id]
    ids.extend(row[0] for row in cur.fetchall())
    return sorted(set(int(value) for value in ids if int(value) > 0))


def get_top_profit_rows(conn, limit: int = 100):
    cur = conn.cursor()
    query = """
        SELECT
            r.output_item_id,
            i.name,
            r.yield,
            op.p50_price,
            op.min_price,
            op.daily_sales,
            COUNT(ri.ingredient_item_id) AS ingredient_count,
            SUM(
                CASE
                    WHEN COALESCE(
                        CASE WHEN ? = 'min' THEN ip.min_price ELSE ip.p50_price END,
                        CASE WHEN ? = 'min' THEN ip.p50_price ELSE ip.min_price END
                    ) IS NULL THEN NULL
                    ELSE ri.qty * COALESCE(
                        CASE WHEN ? = 'min' THEN ip.min_price ELSE ip.p50_price END,
                        CASE WHEN ? = 'min' THEN ip.p50_price ELSE ip.min_price END
                    )
                END
            ) AS material_total
        FROM recipes r
        JOIN items i ON i.item_id = r.output_item_id
        LEFT JOIN recipe_ingredients ri ON ri.output_item_id = r.output_item_id
        LEFT JOIN prices ip ON ip.item_id = ri.ingredient_item_id AND ip.world = ?
        LEFT JOIN prices op ON op.item_id = r.output_item_id AND op.world = ?
        GROUP BY
            r.output_item_id,
            i.name,
            r.yield,
            op.p50_price,
            op.min_price,
            op.daily_sales
        HAVING ingredient_count > 0
           AND COUNT(ip.item_id) = ingredient_count
           AND COALESCE(
                CASE WHEN ? = 'min' THEN op.min_price ELSE op.p50_price END,
                CASE WHEN ? = 'min' THEN op.p50_price ELSE op.min_price END
           ) IS NOT NULL
        ORDER BY (
            COALESCE(
                CASE WHEN ? = 'min' THEN op.min_price ELSE op.p50_price END,
                CASE WHEN ? = 'min' THEN op.p50_price ELSE op.min_price END
            ) - (material_total / r.yield)
        ) DESC
        LIMIT ?;
    """
    cur.execute(
        query,
        (
            BUY_PRICE_FIELD,
            BUY_PRICE_FIELD,
            BUY_PRICE_FIELD,
            BUY_PRICE_FIELD,
            WORLD,
            WORLD,
            SELL_PRICE_FIELD,
            SELL_PRICE_FIELD,
            SELL_PRICE_FIELD,
            SELL_PRICE_FIELD,
            limit,
        ),
    )
    return [
        {
            "item_id": row[0],
            "name": row[1] or f"Item {row[0]}",
            "yield": row[2] or 1,
            "sell_price": choose_price(row[3], row[4], SELL_PRICE_FIELD),
            "daily_sales": normalize_positive_value(row[5]),
            "unit_material_cost": (row[7] / (row[2] or 1)) if row[7] is not None else None,
            "price_gap": choose_price(row[3], row[4], SELL_PRICE_FIELD) - (row[7] / (row[2] or 1)),
        }
        for row in cur.fetchall()
        if row[7] is not None
    ]


def load_dashboard_data():
    init_db()
    tab = request.args.get("tab", "lookup").strip() or "lookup"
    query = request.args.get("q", "").strip()
    selected_id = request.args.get("item_id", "").strip()
    refresh_status = request.args.get("refresh", "").strip()
    refreshed_count = request.args.get("count", "").strip()

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

        top_profits = get_top_profit_rows(conn) if tab == "ranking" else []

    return {
        "world": WORLD,
        "db_path": DB_PATH,
        "counts": counts,
        "prices": prices,
        "recipes": recipes,
        "tab": tab,
        "query": query,
        "search_results": search_results,
        "detail": detail,
        "top_profits": top_profits,
        "sell_price_field": SELL_PRICE_FIELD,
        "buy_price_field": BUY_PRICE_FIELD,
        "fmt_num": fmt_num,
        "refresh_status": refresh_status,
        "refreshed_count": refreshed_count,
    }


@app.route("/")
def index():
    data = load_dashboard_data()
    return render_template("index.html", **data)


@app.post("/refresh-recipe-prices")
def refresh_recipe_prices():
    item_id_raw = request.form.get("item_id", "").strip()
    query = request.form.get("q", "").strip()
    tab = request.form.get("tab", "lookup").strip() or "lookup"
    if not item_id_raw.isdigit():
        return redirect(url_for("index", tab=tab, q=query, refresh="invalid"))

    item_id = int(item_id_raw)
    try:
        init_db()
        with get_conn() as conn:
            ids = get_recipe_item_ids(conn, item_id)
        refreshed = update_prices_for_ids(ids)
        return redirect(url_for("index", tab=tab, q=query, item_id=item_id, refresh="ok", count=refreshed))
    except Exception:
        return redirect(url_for("index", tab=tab, q=query, item_id=item_id, refresh="error"))


@app.route("/health")
def health():
    return {"ok": True}


if __name__ == "__main__":
    app.run(host="127.0.0.1", port=5000, debug=True)
