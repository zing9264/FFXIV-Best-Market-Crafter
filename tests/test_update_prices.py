"""update_prices(aggregated 引擎)的單元測試。

全部離線:用 tests/fixtures/ 的真實 Universalis 回應快照當輸入,
驗證解析、雙口徑列生成、以及寫入 sqlite 的正確性。
"""
from __future__ import annotations

import json
import os
import sqlite3
import tempfile
import unittest
from pathlib import Path

FIXTURES = Path(__file__).parent / "fixtures"


def load_fixture(name: str) -> dict:
    with open(FIXTURES / name, encoding="utf-8") as f:
        return json.load(f)


class BuildRowsTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        import update_prices

        cls.up = update_prices

    def test_world_level_fixture_produces_both_scopes(self):
        payload = load_fixture("aggregated_world_sample.json")
        item = payload["results"][0]
        rows = self.up.build_rows_from_aggregated(item, "鳳凰", {4030: "太陽海岸測試", 4031: "鳳凰"})
        scopes = {row[1] for row in rows}
        self.assertEqual(scopes, {"繁中服", "鳳凰"})

    def test_region_min_uses_cheapest_quality_and_maps_world_name(self):
        item = {
            "itemId": 100,
            "nq": {"minListing": {"region": {"price": 50, "worldId": 4028}, "world": {"price": 60}}},
            "hq": {"minListing": {"region": {"price": 30, "worldId": 4033}, "world": {"price": 90}}},
        }
        rows = self.up.build_rows_from_aggregated(item, "鳳凰", {4028: "伊弗利特", 4033: "巴哈姆特"})
        region = next(r for r in rows if r[1] == "繁中服")
        self.assertEqual(region[5], 30.0)  # min_price 取 nq/hq 較低者
        self.assertEqual(region[3], "巴哈姆特")  # 來源伺服器跟著較低價那筆

    def test_recent_purchase_prefers_newer_timestamp(self):
        item = {
            "itemId": 101,
            "nq": {"recentPurchase": {"world": {"price": 111, "timestamp": 1000}}},
            "hq": {"recentPurchase": {"world": {"price": 222, "timestamp": 2000}}},
        }
        rows = self.up.build_rows_from_aggregated(item, "鳳凰", {})
        world_row = next(r for r in rows if r[1] == "鳳凰")
        self.assertEqual(world_row[6], 222.0)  # sale_price 取時間較新的成交

    def test_layer_without_data_is_skipped(self):
        # 只有區域層有資料:顯示伺服器那列不應生成(避免用 0 覆蓋舊值)
        item = {
            "itemId": 102,
            "nq": {"minListing": {"region": {"price": 5, "worldId": 4031}}},
        }
        rows = self.up.build_rows_from_aggregated(item, "鳳凰", {4031: "鳳凰"})
        self.assertEqual([r[1] for r in rows], ["繁中服"])

    def test_upload_times_normalized_from_milliseconds(self):
        item = {
            "itemId": 103,
            "nq": {"minListing": {"world": {"price": 1}}},
            "worldUploadTimes": [{"worldId": 4031, "timestamp": 1786990837000}],
        }
        rows = self.up.build_rows_from_aggregated(item, "鳳凰", {})
        self.assertEqual(rows[0][9], 1786990837)

    def test_unknown_item_or_empty_payload_yields_nothing(self):
        self.assertEqual(self.up.build_rows_from_aggregated({}, "鳳凰", {}), [])
        self.assertEqual(self.up.build_rows_from_aggregated({"itemId": 1}, "鳳凰", {}), [])


class PersistenceTests(unittest.TestCase):
    """rows 寫入 sqlite 的整合測試(不打網路,直接餵 fixture rows)。"""

    PRICES_DDL = """
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
        )
    """

    def setUp(self):
        # 獨立臨時庫,直接建表 —— 不 reload 全域模組,避免污染同批的其他測試
        self.tmp = tempfile.NamedTemporaryFile(suffix=".sqlite", delete=False)
        self.tmp.close()
        self.db_path = self.tmp.name
        conn = sqlite3.connect(self.db_path)
        conn.execute(self.PRICES_DDL)
        conn.commit()
        conn.close()

    def tearDown(self):
        try:
            os.unlink(self.tmp.name)
        except OSError:
            pass

    def test_rows_upsert_by_item_and_scope(self):
        import update_prices as up

        payload = load_fixture("aggregated_world_sample.json")
        rows = []
        for item in payload["results"]:
            rows.extend(up.build_rows_from_aggregated(item, "鳳凰", {4031: "鳳凰"}))

        conn = sqlite3.connect(self.db_path)
        conn.executemany(
            """
            INSERT OR REPLACE INTO prices(
                item_id, world, world_id, world_name, p50_price, min_price, sale_price, listings, daily_sales, last_updated
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?);
            """,
            rows,
        )
        conn.commit()

        count = conn.execute("SELECT COUNT(*) FROM prices").fetchone()[0]
        self.assertEqual(count, len(rows))

        # 重複寫入同一批 -> 筆數不變(主鍵 item_id+world upsert)
        conn.executemany(
            """
            INSERT OR REPLACE INTO prices(
                item_id, world, world_id, world_name, p50_price, min_price, sale_price, listings, daily_sales, last_updated
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?);
            """,
            rows,
        )
        conn.commit()
        count2 = conn.execute("SELECT COUNT(*) FROM prices").fetchone()[0]
        self.assertEqual(count2, count)
        conn.close()


if __name__ == "__main__":
    unittest.main()
