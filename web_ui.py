from __future__ import annotations

import datetime as dt

from flask import Flask, render_template

from config import DB_PATH, WORLD
from db import get_conn, init_db


app = Flask(__name__)


def fmt_ts(ts: int | None) -> str:
    if not ts:
        return "-"
    return dt.datetime.utcfromtimestamp(ts).strftime("%Y-%m-%d %H:%M:%S UTC")


def load_dashboard_data():
    init_db()
    with get_conn() as conn:
        cur = conn.cursor()

        cur.execute("SELECT COUNT(*) FROM items;")
        items_count = cur.fetchone()[0]

        cur.execute("SELECT COUNT(*) FROM recipes;")
        recipes_count = cur.fetchone()[0]

        cur.execute("SELECT COUNT(*) FROM recipe_ingredients;")
        ingredients_count = cur.fetchone()[0]

        cur.execute("SELECT COUNT(*) FROM prices WHERE world=?;", (WORLD,))
        prices_count = cur.fetchone()[0]

        cur.execute(
            """
            SELECT p.item_id, i.name, p.p50_price, p.min_price, p.listings, p.last_updated
            FROM prices p
            LEFT JOIN items i ON i.item_id = p.item_id
            WHERE p.world=?
            ORDER BY p.last_updated DESC NULLS LAST
            LIMIT 50;
            """,
            (WORLD,),
        )
        prices = [
            {
                "item_id": row[0],
                "name": row[1] or f"Item {row[0]}",
                "p50_price": row[2],
                "min_price": row[3],
                "listings": row[4],
                "last_updated": fmt_ts(row[5]),
            }
            for row in cur.fetchall()
        ]

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

    return {
        "world": WORLD,
        "db_path": DB_PATH,
        "counts": {
            "items": items_count,
            "recipes": recipes_count,
            "ingredients": ingredients_count,
            "prices": prices_count,
        },
        "prices": prices,
        "recipes": recipes,
    }


@app.route("/")
def index():
    data = load_dashboard_data()
    return render_template("index.html", **data)


@app.route("/health")
def health():
    return {"ok": True}


if __name__ == "__main__":
    app.run(host="127.0.0.1", port=5000, debug=True)
