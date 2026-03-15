from __future__ import annotations

import importlib
import os
import sqlite3
import tempfile
import unittest
from unittest.mock import patch


def seed_db(db_path: str) -> None:
    conn = sqlite3.connect(db_path)
    cur = conn.cursor()
    cur.executescript(
        """
        CREATE TABLE items (
            item_id INTEGER PRIMARY KEY,
            name TEXT
        );

        CREATE TABLE recipes (
            output_item_id INTEGER PRIMARY KEY,
            yield INTEGER
        );

        CREATE TABLE recipe_ingredients (
            output_item_id INTEGER,
            ingredient_item_id INTEGER,
            qty INTEGER,
            PRIMARY KEY (output_item_id, ingredient_item_id)
        );

        CREATE TABLE prices (
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
    cur.executemany(
        "INSERT INTO items(item_id, name) VALUES(?, ?);",
        [
            (100, "測試成品"),
            (200, "素材甲"),
            (300, "素材乙"),
            (999, "其他項目"),
        ],
    )
    cur.execute("INSERT INTO recipes(output_item_id, yield) VALUES(100, 1);")
    cur.executemany(
        "INSERT INTO recipe_ingredients(output_item_id, ingredient_item_id, qty) VALUES(?, ?, ?);",
        [
            (100, 200, 2),
            (100, 300, 3),
        ],
    )
    cur.execute(
        """
        INSERT INTO prices(
            item_id, world, world_id, world_name, p50_price, min_price, sale_price, listings, daily_sales, last_updated
        ) VALUES(100, 'Phoenix', 0, 'Asura', 1500, 1200, 1333, 4, 1.5, 1234567890);
        """
    )
    conn.commit()
    conn.close()


class RefreshRecipePricesRouteTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.temp_dir = tempfile.TemporaryDirectory()
        cls.db_path = os.path.join(cls.temp_dir.name, "test.sqlite")
        seed_db(cls.db_path)

        os.environ["FF14_DB_PATH"] = cls.db_path
        os.environ["FF14_WORLD"] = "Phoenix"

        import config
        import db
        import update_prices
        import web_ui

        cls.config = importlib.reload(config)
        cls.db = importlib.reload(db)
        cls.update_prices = importlib.reload(update_prices)
        cls.web_ui = importlib.reload(web_ui)
        cls.app = cls.web_ui.app
        cls.app.config["TESTING"] = True

    @classmethod
    def tearDownClass(cls):
        cls.temp_dir.cleanup()
        os.environ.pop("FF14_DB_PATH", None)
        os.environ.pop("FF14_WORLD", None)

    def test_get_recipe_item_ids_returns_product_and_ingredients(self):
        with self.db.get_conn() as conn:
            item_ids = self.web_ui.get_recipe_item_ids(conn, 100)
        self.assertEqual(item_ids, [100, 200, 300])

    def test_refresh_route_rejects_invalid_item_id(self):
        client = self.app.test_client()
        response = client.post(
            "/refresh-recipe-prices",
            data={"item_id": "", "q": "測試", "tab": "lookup"},
            follow_redirects=False,
        )
        self.assertEqual(response.status_code, 302)
        self.assertIn("refresh=invalid", response.headers["Location"])

    def test_refresh_route_updates_only_recipe_items(self):
        client = self.app.test_client()
        with patch.object(self.web_ui, "update_prices_for_ids", return_value=3) as mock_update:
            response = client.post(
                "/refresh-recipe-prices",
                data={"item_id": "100", "q": "測試成品", "tab": "lookup"},
                follow_redirects=False,
            )

        self.assertEqual(response.status_code, 302)
        self.assertIn("refresh=ok", response.headers["Location"])
        self.assertIn("count=3", response.headers["Location"])
        mock_update.assert_called_once_with([100, 200, 300])

    def test_choose_price_and_daily_sales_ignore_zero_defaults(self):
        self.assertEqual(self.web_ui.choose_price(0, 500, "p50"), 500)
        self.assertEqual(self.web_ui.choose_price(800, 0, "min"), 800)
        self.assertIsNone(self.web_ui.choose_price(0, 0, "p50"))
        self.assertIsNone(self.web_ui.normalize_positive_value(0))
        self.assertEqual(self.web_ui.normalize_positive_value(2.5), 2.5)

    def test_load_recipe_detail_includes_product_sale_price(self):
        with self.db.get_conn() as conn:
            detail = self.web_ui.load_recipe_detail(conn, 100)
        self.assertIsNotNone(detail)
        self.assertEqual(detail["product"]["sale_price"], 1333)
        self.assertEqual(detail["product"]["world_name"], "Asura")


if __name__ == "__main__":
    unittest.main()
