from __future__ import annotations

import time

from config import DISPLAY_WORLD, LOWEST_WORLD
from db import get_conn, init_db


def rebuild_profits() -> int:
    init_db()
    now = int(time.time())

    with get_conn() as conn:
        cur = conn.cursor()
        cur.execute("DELETE FROM profits WHERE world=?;", (DISPLAY_WORLD,))
        cur.execute(
            """
            INSERT OR REPLACE INTO profits(
                item_id,
                world,
                world_name,
                listing_price,
                sale_price,
                material_total,
                unit_material_cost,
                profit_by_listing,
                profit_by_sale,
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
                dp.min_price - (SUM(ri.qty * fp.min_price) / r.yield) AS profit_by_listing,
                CASE
                    WHEN dp.sale_price > 0 THEN dp.sale_price - (SUM(ri.qty * fp.min_price) / r.yield)
                    ELSE 0
                END AS profit_by_sale,
                dp.daily_sales,
                ?
            FROM recipes r
            JOIN recipe_ingredients ri ON ri.output_item_id = r.output_item_id
            JOIN prices fp
              ON fp.item_id = ri.ingredient_item_id
             AND fp.world = ?
             AND fp.min_price > 0
            JOIN prices dp
              ON dp.item_id = r.output_item_id
             AND dp.world = ?
             AND dp.min_price > 0
            GROUP BY
                r.output_item_id,
                r.yield,
                dp.world_name,
                dp.min_price,
                dp.sale_price,
                dp.daily_sales
            HAVING COUNT(*) = (
                SELECT COUNT(*)
                FROM recipe_ingredients ri2
                WHERE ri2.output_item_id = r.output_item_id
            );
            """,
            (
                DISPLAY_WORLD,
                DISPLAY_WORLD,
                now,
                LOWEST_WORLD,
                DISPLAY_WORLD,
            ),
        )
        return cur.rowcount if cur.rowcount is not None else 0


def main() -> int:
    updated = rebuild_profits()
    print(f"Updated profits: {updated} rows for display world {DISPLAY_WORLD}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
