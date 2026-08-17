from __future__ import annotations

import time
from typing import Optional

from config import DISPLAY_WORLD, LOWEST_WORLD
from db import get_conn, get_setting, init_db


def resolve_display_world() -> str:
    """顯示伺服器:使用者設定優先,未設定回退 config 預設(鳳凰)。"""
    return get_setting("display_world") or DISPLAY_WORLD


def profit_worlds(display_world: Optional[str] = None) -> list[str]:
    display = display_world or resolve_display_world()
    return list(dict.fromkeys([LOWEST_WORLD, display]))


def rebuild_profits(display_world: Optional[str] = None) -> int:
    init_db()
    now = int(time.time())
    display = display_world or resolve_display_world()
    worlds = profit_worlds(display)

    with get_conn() as conn:
        cur = conn.cursor()
        placeholders = ",".join("?" for _ in worlds)
        cur.execute(f"DELETE FROM profits WHERE world IN ({placeholders});", tuple(worlds))
        total_updated = 0
        for material_world in worlds:
            # ing_counts CTE 取代原本的相關子查詢(HAVING 內每組重查一次
            # recipe_ingredients),12k 配方省下 12k 次子查詢
            cur.execute(
                """
                WITH ing_counts AS (
                    SELECT output_item_id, COUNT(*) AS n
                    FROM recipe_ingredients
                    GROUP BY output_item_id
                )
                INSERT OR REPLACE INTO profits(
                    item_id,
                    world,
                    world_name,
                    listing_price,
                    sale_price,
                    material_total,
                    unit_material_cost,
                    display_unit_material_cost,
                    profit_by_listing,
                    profit_by_sale,
                    profit_margin_pct,
                    sale_margin_pct,
                    daily_sales,
                    updated
                )
                SELECT
                    r.output_item_id,
                    ?,
                    COALESCE(dp.world_name, ?),
                    dp.min_price,
                    dp.sale_price,
                    SUM(ri.qty * fp.min_price) AS material_total,
                    SUM(ri.qty * fp.min_price) / r.yield AS unit_material_cost,
                    CASE
                        WHEN COUNT(pp.item_id) = COUNT(*)
                        THEN SUM(ri.qty * pp.min_price) / r.yield
                        ELSE 0
                    END AS display_unit_material_cost,
                    dp.min_price - (SUM(ri.qty * fp.min_price) / r.yield) AS profit_by_listing,
                    CASE
                        WHEN dp.sale_price > 0 THEN dp.sale_price - (SUM(ri.qty * fp.min_price) / r.yield)
                        ELSE 0
                    END AS profit_by_sale,
                    CASE
                        WHEN (SUM(ri.qty * fp.min_price) / r.yield) > 0
                        THEN ((dp.min_price - (SUM(ri.qty * fp.min_price) / r.yield)) / (SUM(ri.qty * fp.min_price) / r.yield)) * 100.0
                        ELSE 0
                    END AS profit_margin_pct,
                    CASE
                        WHEN dp.sale_price > 0 AND (SUM(ri.qty * fp.min_price) / r.yield) > 0
                        THEN ((dp.sale_price - (SUM(ri.qty * fp.min_price) / r.yield)) / (SUM(ri.qty * fp.min_price) / r.yield)) * 100.0
                        ELSE 0
                    END AS sale_margin_pct,
                    dp.daily_sales,
                    ?
                FROM recipes r
                JOIN ing_counts ic ON ic.output_item_id = r.output_item_id
                JOIN recipe_ingredients ri ON ri.output_item_id = r.output_item_id
                JOIN prices fp
                  ON fp.item_id = ri.ingredient_item_id
                 AND fp.world = ?
                 AND fp.min_price > 0
                JOIN prices dp
                  ON dp.item_id = r.output_item_id
                 AND dp.world = ?
                 AND dp.min_price > 0
                LEFT JOIN prices pp
                  ON pp.item_id = ri.ingredient_item_id
                 AND pp.world = ?
                 AND pp.min_price > 0
                GROUP BY
                    r.output_item_id,
                    r.yield,
                    ic.n,
                    dp.world_name,
                    dp.min_price,
                    dp.sale_price,
                    dp.daily_sales
                HAVING COUNT(*) = ic.n;
                """,
                (
                    material_world,
                    display,
                    now,
                    material_world,
                    display,
                    display,
                ),
            )
            # CTE + INSERT...SELECT 下 cursor.rowcount 不可靠(回 -1),改用 changes()
            total_updated += int(cur.execute("SELECT changes();").fetchone()[0])
        return total_updated


def main() -> int:
    updated = rebuild_profits()
    print(f"Updated profits: {updated} rows for display world {resolve_display_world()}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
