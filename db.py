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
                world_id INTEGER,
                p50_price REAL,
                min_price REAL,
                listings INTEGER,
                last_updated INTEGER,
                PRIMARY KEY (item_id, world)
            );
            """
        )

        cur.execute(
            """
            CREATE TABLE IF NOT EXISTS profits (
                item_id INTEGER PRIMARY KEY,
                cost REAL,
                profit REAL,
                updated INTEGER
            );
            """
        )

        cur.execute("CREATE INDEX IF NOT EXISTS idx_recipe_ingredients_output ON recipe_ingredients(output_item_id);")
        cur.execute("CREATE INDEX IF NOT EXISTS idx_recipe_ingredients_ingredient ON recipe_ingredients(ingredient_item_id);")
        cur.execute("CREATE INDEX IF NOT EXISTS idx_prices_world_item ON prices(world, item_id);")
