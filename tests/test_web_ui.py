from __future__ import annotations

import importlib
import os
import sqlite3
import tempfile
import time
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

        CREATE TABLE profits (
            item_id INTEGER,
            world TEXT,
            world_name TEXT DEFAULT '',
            listing_price REAL DEFAULT 0,
            sale_price REAL DEFAULT 0,
            material_total REAL DEFAULT 0,
            unit_material_cost REAL DEFAULT 0,
            display_unit_material_cost REAL DEFAULT 0,
            profit_by_listing REAL DEFAULT 0,
            profit_by_sale REAL DEFAULT 0,
            profit_margin_pct REAL DEFAULT 0,
            sale_margin_pct REAL DEFAULT 0,
            daily_sales REAL DEFAULT 0,
            updated INTEGER DEFAULT 0,
            PRIMARY KEY (item_id, world)
        );

        CREATE TABLE collectable_rewards (
            item_id INTEGER PRIMARY KEY,
            purple_scrips INTEGER DEFAULT 0,
            orange_scrips INTEGER DEFAULT 0,
            class_job_level INTEGER DEFAULT 0,
            recipe_level_table INTEGER DEFAULT 0,
            craft_type INTEGER DEFAULT -1
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
            (500, "收藏用測試品"),
            (501, "收藏素材甲"),
            (502, "收藏素材乙"),
        ],
    )
    cur.execute("INSERT INTO recipes(output_item_id, yield) VALUES(100, 1);")
    cur.execute("INSERT INTO recipes(output_item_id, yield) VALUES(500, 1);")
    cur.executemany(
        "INSERT INTO recipe_ingredients(output_item_id, ingredient_item_id, qty) VALUES(?, ?, ?);",
        [
            (100, 200, 2),
            (100, 300, 3),
            (500, 501, 2),
            (500, 502, 1),
        ],
    )
    cur.execute(
        """
        INSERT INTO prices(
            item_id, world, world_id, world_name, p50_price, min_price, sale_price, listings, daily_sales, last_updated
        ) VALUES(100, '繁中服', 0, '鳳凰', 1500, 1200, 1333, 4, 1.5, 1234567890);
        """
    )
    cur.execute(
        """
        INSERT INTO prices(
            item_id, world, world_id, world_name, p50_price, min_price, sale_price, listings, daily_sales, last_updated
        ) VALUES(100, '鳳凰', 0, '鳳凰', 1500, 1300, 1333, 2, 1.1, 1234567890);
        """
    )
    cur.executemany(
        """
        INSERT INTO prices(
            item_id, world, world_id, world_name, p50_price, min_price, sale_price, listings, daily_sales, last_updated
        ) VALUES(?, '繁中服', 0, ?, 0, ?, 0, 0, 0, 1234567890);
        """,
        [
            (200, "鳳凰", 50),
            (300, "巴哈姆特", 20),
            (501, "鳳凰", 100),
            (502, "巴哈姆特", 50),
        ],
    )
    cur.execute(
        """
        INSERT INTO prices(
            item_id, world, world_id, world_name, p50_price, min_price, sale_price, listings, daily_sales, last_updated
        ) VALUES(200, '鳳凰', 0, '鳳凰', 0, 55, 0, 0, 0, 1234567890);
        """
    )
    cur.executemany(
        """
        INSERT INTO profits(
            item_id, world, world_name, listing_price, sale_price, material_total, unit_material_cost, display_unit_material_cost,
            profit_by_listing, profit_by_sale, profit_margin_pct, sale_margin_pct, daily_sales, updated
        ) VALUES(?, ?, '鳳凰', ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, 1234567890);
        """,
        [
            (100, "繁中服", 2500, 2200, 2000, 2000, 2300, 500, 200, 25, 10, 1.2),
            (100, "鳳凰", 2500, 2200, 2300, 2300, 2300, 200, 0, 8, 0, 1.2),
            (999, "繁中服", 400, 350, 200, 200, 250, 200, 150, 100, 75, 0.4),
        ],
    )
    cur.execute(
        """
        INSERT INTO collectable_rewards(item_id, purple_scrips, orange_scrips, class_job_level, recipe_level_table, craft_type)
        VALUES(500, 95, 0, 90, 999, 0);
        """
    )
    conn.commit()
    conn.close()


class RefreshRecipePricesRouteTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.temp_dir = tempfile.TemporaryDirectory()
        cls.db_path = os.path.join(cls.temp_dir.name, "test.sqlite")
        cls.app_log_path = os.path.join(cls.temp_dir.name, "app.log")
        cls.refresh_stats_path = os.path.join(cls.temp_dir.name, "refresh_stats.jsonl")
        cls.collectable_rewards_path = os.path.join(cls.temp_dir.name, "collectable_rewards.csv")
        seed_db(cls.db_path)
        with open(cls.collectable_rewards_path, "w", encoding="utf-8", newline="") as handle:
            handle.write("item_id,name,purple_scrips,orange_scrips,class_job_level,recipe_level_table,craft_type\n")
            handle.write("500,收藏用測試品,95,0,90,999,0\n")

        os.environ["FF14_DB_PATH"] = cls.db_path
        os.environ["FF14_WORLD"] = "繁中服"
        os.environ["FF14_LOWEST_WORLD"] = "繁中服"
        os.environ["FF14_DISPLAY_WORLD"] = "鳳凰"
        os.environ["FF14_RECIPE_REFRESH_COOLDOWN_SECONDS"] = "30"
        os.environ["FF14_FULL_REFRESH_COOLDOWN_SECONDS"] = "600"
        os.environ["FF14_APP_LOG_PATH"] = cls.app_log_path
        os.environ["FF14_REFRESH_STATS_PATH"] = cls.refresh_stats_path
        os.environ["FF14_COLLECTABLE_REWARDS_CSV_PATH"] = cls.collectable_rewards_path

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
        os.environ.pop("FF14_LOWEST_WORLD", None)
        os.environ.pop("FF14_DISPLAY_WORLD", None)
        os.environ.pop("FF14_RECIPE_REFRESH_COOLDOWN_SECONDS", None)
        os.environ.pop("FF14_FULL_REFRESH_COOLDOWN_SECONDS", None)
        os.environ.pop("FF14_APP_LOG_PATH", None)
        os.environ.pop("FF14_REFRESH_STATS_PATH", None)
        os.environ.pop("FF14_COLLECTABLE_REWARDS_CSV_PATH", None)

    def setUp(self):
        self.web_ui.cooldown_state["last_recipe_refresh_at"] = 0.0
        self.web_ui.cooldown_state["last_full_refresh_at"] = 0.0
        self.web_ui.refresh_state.update(
            {
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
        )
        for path in (self.app_log_path, self.refresh_stats_path):
            if os.path.exists(path):
                os.remove(path)

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
        with patch.object(self.web_ui, "update_prices_for_worlds", return_value=6) as mock_update:
            response = client.post(
                "/refresh-recipe-prices",
                data={"item_id": "100", "q": "測試成品", "tab": "lookup"},
                follow_redirects=False,
            )

        self.assertEqual(response.status_code, 302)
        self.assertIn("refresh=ok", response.headers["Location"])
        self.assertIn("count=6", response.headers["Location"])
        mock_update.assert_called_once_with([100, 200, 300], ["繁中服", "鳳凰"])

    def test_refresh_route_respects_recipe_cooldown(self):
        client = self.app.test_client()
        self.web_ui.cooldown_state["last_recipe_refresh_at"] = time.time()
        response = client.post(
            "/refresh-recipe-prices",
            data={"item_id": "100", "q": "測試成品", "tab": "lookup"},
            follow_redirects=False,
        )
        self.assertEqual(response.status_code, 302)
        self.assertIn("refresh=cooldown_recipe", response.headers["Location"])

    def test_normalize_nonzero_value_ignores_zero_defaults(self):
        self.assertEqual(self.web_ui.normalize_nonzero_value(500), 500)
        self.assertIsNone(self.web_ui.normalize_nonzero_value(0))
        self.assertIsNone(self.web_ui.normalize_nonzero_value(None))
        self.assertEqual(self.web_ui.normalize_nonzero_value(2.5), 2.5)

    def test_load_recipe_detail_includes_product_sale_price(self):
        with self.db.get_conn() as conn:
            detail = self.web_ui.load_recipe_detail(conn, 100)
        self.assertIsNotNone(detail)
        self.assertEqual(detail["product"]["sale_price"], 1333)
        self.assertEqual(detail["product"]["world_name"], "鳳凰")
        ingredient_200 = next(ing for ing in detail["ingredients"] if ing["item_id"] == 200)
        self.assertEqual(ingredient_200["display_world_name"], "鳳凰")
        self.assertEqual(ingredient_200["display_price"], 55)

    def test_ranking_page_renders(self):
        client = self.app.test_client()
        response = client.get("/?tab=ranking")
        self.assertEqual(response.status_code, 200)

    def test_ranking_page_supports_margin_sort_and_sales_filter(self):
        client = self.app.test_client()
        response = client.get("/?tab=ranking&sort=margin&min_daily_sales=0.5")
        body = response.get_data(as_text=True)

        self.assertEqual(response.status_code, 200)
        self.assertIn("當前獲利%", body)
        self.assertIn("測試成品", body)
        self.assertNotIn("其他項目", body)
        self.assertIn("單件成本(鳳凰)", body)

    def test_ranking_page_supports_past_profit_sort(self):
        client = self.app.test_client()
        response = client.get("/?tab=ranking&sort=past_profit&min_past_profit=100")
        body = response.get_data(as_text=True)

        self.assertEqual(response.status_code, 200)
        self.assertIn("過去獲利", body)
        self.assertIn("測試成品", body)

    def test_ranking_page_supports_price_scope_switch(self):
        client = self.app.test_client()
        response = client.get("/?tab=ranking&price_scope=phoenix")
        body = response.get_data(as_text=True)

        self.assertEqual(response.status_code, 200)
        self.assertIn("單件素材成本(鳳凰)", body)
        self.assertIn("2,300", body)

    def test_collectables_page_renders_cost_per_scrip(self):
        client = self.app.test_client()
        response = client.get("/?tab=collectables&collectable_sort=desc")
        body = response.get_data(as_text=True)

        self.assertEqual(response.status_code, 200)
        self.assertIn("收藏品成本", body)
        self.assertIn("收藏用測試品", body)
        self.assertIn("木工", body)

    def test_collectables_page_supports_price_scope_switch(self):
        client = self.app.test_client()
        response = client.get("/?tab=collectables&collectable_sort=desc&price_scope=phoenix")
        body = response.get_data(as_text=True)

        self.assertEqual(response.status_code, 200)
        self.assertIn("每張紫票成本(鳳凰)", body)

    def test_collectables_page_defaults_to_purple_scrip(self):
        """The seeded fixture only has a purple-scrip reward (lv90 → 95 purple),
        so the default view (which equals `scrip_type=purple`) should render the
        purple cost label and include the purple-scrip reward value."""
        client = self.app.test_client()
        response = client.get("/?tab=collectables")
        body = response.get_data(as_text=True)

        self.assertEqual(response.status_code, 200)
        self.assertIn("收藏品成本(紫票)", body)
        # The seeded reward amount should appear in the scrip amount column.
        self.assertIn(">95<", body)

    def test_collectables_page_orange_scope_filters_purple_only_data(self):
        """With only a purple-scrip fixture loaded, asking for the orange view
        should return a well-formed page that reports no matching rows — not
        400, not a stale purple label. This guards against the old bug where
        lv100 items silently showed up with a stub purple value of 45."""
        client = self.app.test_client()
        response = client.get("/?tab=collectables&scrip_type=orange")
        body = response.get_data(as_text=True)

        self.assertEqual(response.status_code, 200)
        self.assertIn("收藏品成本(橘票)", body)
        self.assertIn("每張橘票成本", body)
        self.assertIn("目前沒有可計算的橘票收藏品資料", body)

    def test_refresh_profits_redirects_with_count(self):
        client = self.app.test_client()
        with patch.object(self.web_ui, "rebuild_profits", return_value=12) as mock_rebuild:
            response = client.post(
                "/refresh-profits",
                data={"page": "2", "sort": "margin", "min_daily_sales": "0.5"},
                follow_redirects=False,
            )

        self.assertEqual(response.status_code, 302)
        self.assertIn("tab=ranking", response.headers["Location"])
        self.assertIn("page=2", response.headers["Location"])
        self.assertIn("sort=margin", response.headers["Location"])
        self.assertIn("min_daily_sales=0.5", response.headers["Location"])
        self.assertIn("refresh=ok", response.headers["Location"])
        self.assertIn("count=12", response.headers["Location"])
        mock_rebuild.assert_called_once_with()

    def test_refresh_all_prices_respects_full_cooldown(self):
        client = self.app.test_client()
        self.web_ui.cooldown_state["last_full_refresh_at"] = time.time()
        response = client.post(
            "/refresh-all-prices",
            data={"tab": "lookup"},
            follow_redirects=False,
        )
        self.assertEqual(response.status_code, 302)
        self.assertIn("refresh=cooldown_full", response.headers["Location"])

    def test_cancel_refresh_sets_cancel_requested_when_running(self):
        client = self.app.test_client()
        self.web_ui.refresh_state["running"] = True

        response = client.post(
            "/cancel-refresh",
            data={"tab": "lookup"},
            follow_redirects=False,
        )

        self.assertEqual(response.status_code, 302)
        self.assertIn("refresh=cancel_requested", response.headers["Location"])
        self.assertTrue(self.web_ui.refresh_state["cancel_requested"])

    def test_cancel_refresh_rejects_when_not_running(self):
        client = self.app.test_client()
        response = client.post(
            "/cancel-refresh",
            data={"tab": "lookup"},
            follow_redirects=False,
        )

        self.assertEqual(response.status_code, 302)
        self.assertIn("refresh=not_running", response.headers["Location"])

    def test_download_app_log_returns_file(self):
        client = self.app.test_client()
        self.web_ui.append_app_log("測試 app log")

        response = client.get("/logs/app")
        try:
            self.assertEqual(response.status_code, 200)
            self.assertIn("attachment", response.headers.get("Content-Disposition", ""))
            self.assertIn("測試 app log", response.get_data(as_text=True))
        finally:
            response.close()

    def test_count_recent_sales_uses_history_entries(self):
        now = int(time.time())
        entries = [
            {"timestamp": now - (1 * 24 * 60 * 60)},
            {"timestamp": now - (2 * 24 * 60 * 60)},
            {"timestamp": now - (4 * 24 * 60 * 60)},
        ]
        self.assertEqual(self.update_prices.count_recent_sales(entries, days=3), 2)

    def test_download_refresh_stats_returns_file(self):
        client = self.app.test_client()
        self.web_ui.append_refresh_stats("world_progress", world="繁中服", stats={"http_504": 2})

        response = client.get("/logs/refresh-stats")
        try:
            self.assertEqual(response.status_code, 200)
            self.assertIn("attachment", response.headers.get("Content-Disposition", ""))
            self.assertIn("\"event\": \"world_progress\"", response.get_data(as_text=True))
        finally:
            response.close()


if __name__ == "__main__":
    unittest.main()
