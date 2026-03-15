from __future__ import annotations

import sqlite3
from contextlib import contextmanager

from config import DB_PATH


@contextmanager
def get_conn():
    conn = sqlite3.connect(DB_PATH)
    try:
        yield conn
        conn.commit()
    finally:
        conn.close()


def init_db():
    with get_conn() as conn:
        cur = conn.cursor()

        cur.execute(
            """
            CREATE TABLE IF NOT EXISTS items (
                item_id INTEGER PRIMARY KEY,
                name TEXT
            );
            """
        )

        cur.execute(
            """
            CREATE TABLE IF NOT EXISTS recipes (
                output_item_id INTEGER PRIMARY KEY,
                yield INTEGER
            );
            """
        )

        cur.execute(
            """
            CREATE TABLE IF NOT EXISTS recipe_ingredients (
                output_item_id INTEGER,
                ingredient_item_id INTEGER,
                qty INTEGER,
                PRIMARY KEY (output_item_id, ingredient_item_id)
            );
            """
        )

        cur.execute(
            """
            CREATE TABLE IF NOT EXISTS prices (
                item_id INTEGER,
                world TEXT,
                world_id INTEGER DEFAULT 0,
                world_name TEXT DEFAULT '',
                p50_price REAL DEFAULT 0,
                min_price REAL DEFAULT 0,
                sale_price REAL DEFAULT 0,
                listings INTEGER DEFAULT 0,
                daily_sales REAL DEFAULT 0,
                last_updated INTEGER DEFAULT 0,
                PRIMARY KEY (item_id, world)
            );
            """
        )

        cur.execute(
            """
            CREATE TABLE IF NOT EXISTS profits (
                item_id INTEGER,
                world TEXT,
                world_name TEXT DEFAULT '',
                listing_price REAL DEFAULT 0,
                sale_price REAL DEFAULT 0,
                material_total REAL DEFAULT 0,
                unit_material_cost REAL DEFAULT 0,
                profit_by_listing REAL DEFAULT 0,
                profit_by_sale REAL DEFAULT 0,
                daily_sales REAL DEFAULT 0,
                updated INTEGER DEFAULT 0,
                PRIMARY KEY (item_id, world)
            );
            """
        )

        cur.execute("CREATE INDEX IF NOT EXISTS idx_recipe_ingredients_output ON recipe_ingredients(output_item_id);")
        cur.execute("CREATE INDEX IF NOT EXISTS idx_recipe_ingredients_ingredient ON recipe_ingredients(ingredient_item_id);")
        cur.execute("PRAGMA table_info(prices);")
        price_info = {row[1]: row for row in cur.fetchall()}
        expected_defaults = {
            "world_id": "0",
            "world_name": "''",
            "p50_price": "0",
            "min_price": "0",
            "sale_price": "0",
            "listings": "0",
            "daily_sales": "0",
            "last_updated": "0",
        }
        rebuild_prices = any(
            name not in price_info or str(price_info[name][4]) != default
            for name, default in expected_defaults.items()
        )

        if rebuild_prices:
            cur.execute(
                """
                CREATE TABLE prices_new (
                    item_id INTEGER,
                    world TEXT,
                    world_id INTEGER DEFAULT 0,
                    world_name TEXT DEFAULT '',
                    p50_price REAL DEFAULT 0,
                    min_price REAL DEFAULT 0,
                    sale_price REAL DEFAULT 0,
                    listings INTEGER DEFAULT 0,
                    daily_sales REAL DEFAULT 0,
                    last_updated INTEGER DEFAULT 0,
                    PRIMARY KEY (item_id, world)
                );
                """
            )
            existing_columns = {name for name in price_info}
            cur.execute(
                f"""
                INSERT INTO prices_new(
                    item_id, world, world_id, world_name, p50_price, min_price, sale_price, listings, daily_sales, last_updated
                )
                SELECT
                    item_id,
                    world,
                    COALESCE(world_id, 0),
                    COALESCE({"world_name" if "world_name" in existing_columns else "''"}, ''),
                    COALESCE(p50_price, 0),
                    COALESCE(min_price, 0),
                    COALESCE({"sale_price" if "sale_price" in existing_columns else "0"}, 0),
                    COALESCE(listings, 0),
                    COALESCE({"daily_sales" if "daily_sales" in existing_columns else "0"}, 0),
                    COALESCE(last_updated, 0)
                FROM prices;
                """
            )
            cur.execute("DROP TABLE prices;")
            cur.execute("ALTER TABLE prices_new RENAME TO prices;")

        cur.execute("CREATE INDEX IF NOT EXISTS idx_prices_world_item ON prices(world, item_id);")
        cur.execute("PRAGMA table_info(profits);")
        profit_info = {row[1]: row for row in cur.fetchall()}
        expected_profit_columns = [
            "item_id",
            "world",
            "world_name",
            "listing_price",
            "sale_price",
            "material_total",
            "unit_material_cost",
            "profit_by_listing",
            "profit_by_sale",
            "daily_sales",
            "updated",
        ]
        if list(profit_info) != expected_profit_columns:
            cur.execute("DROP TABLE IF EXISTS profits;")
            cur.execute(
                """
                CREATE TABLE profits (
                    item_id INTEGER,
                    world TEXT,
                    world_name TEXT DEFAULT '',
                    listing_price REAL DEFAULT 0,
                    sale_price REAL DEFAULT 0,
                    material_total REAL DEFAULT 0,
                    unit_material_cost REAL DEFAULT 0,
                    profit_by_listing REAL DEFAULT 0,
                    profit_by_sale REAL DEFAULT 0,
                    daily_sales REAL DEFAULT 0,
                    updated INTEGER DEFAULT 0,
                    PRIMARY KEY (item_id, world)
                );
                """
            )
        cur.execute("CREATE INDEX IF NOT EXISTS idx_profits_world_listing ON profits(world, profit_by_listing DESC);")
