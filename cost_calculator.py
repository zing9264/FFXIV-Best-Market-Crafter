from __future__ import annotations

from dataclasses import dataclass

from config import BUY_PRICE_FIELD, DB_PATH, WORLD
from db import get_conn, init_db


@dataclass
class CostResult:
    item_id: int
    cost: float | None


class CostCalculator:
    def __init__(self, world: str = WORLD):
        self.world = world
        self.memo = {}
        self.visiting = set()

    def _get_market_price(self, conn, item_id: int) -> float | None:
        cur = conn.cursor()
        cur.execute(
            "SELECT p50_price, min_price FROM prices WHERE item_id=? AND world=?;",
            (item_id, self.world),
        )
        row = cur.fetchone()
        if not row:
            return None

        p50_price, min_price = row
        if BUY_PRICE_FIELD == "min":
            return min_price if min_price is not None else p50_price
        return p50_price if p50_price is not None else min_price

    def _get_recipe(self, conn, item_id: int):
        cur = conn.cursor()
        cur.execute("SELECT yield FROM recipes WHERE output_item_id=?;", (item_id,))
        row = cur.fetchone()
        if not row:
            return None, []
        yield_qty = row[0] or 1
        cur.execute(
            "SELECT ingredient_item_id, qty FROM recipe_ingredients WHERE output_item_id=?;",
            (item_id,),
        )
        ingredients = cur.fetchall()
        return yield_qty, ingredients

    def cost(self, item_id: int) -> float | None:
        if item_id in self.memo:
            return self.memo[item_id]
        if item_id in self.visiting:
            # Cycle detected; fallback to market price only
            with get_conn() as conn:
                price = self._get_market_price(conn, item_id)
            self.memo[item_id] = price
            return price

        self.visiting.add(item_id)
        with get_conn() as conn:
            market_price = self._get_market_price(conn, item_id)
            yield_qty, ingredients = self._get_recipe(conn, item_id)

        craft_cost = None
        if ingredients:
            total = 0.0
            for ing_id, qty in ingredients:
                ing_cost = self.cost(int(ing_id))
                if ing_cost is None:
                    total = None
                    break
                total += ing_cost * qty
            if total is not None:
                craft_cost = total / (yield_qty or 1)

        if market_price is None and craft_cost is None:
            result = None
        elif market_price is None:
            result = craft_cost
        elif craft_cost is None:
            result = market_price
        else:
            result = min(market_price, craft_cost)

        self.memo[item_id] = result
        self.visiting.remove(item_id)
        return result


def compute_cost(item_id: int) -> CostResult:
    init_db()
    calculator = CostCalculator()
    return CostResult(item_id=item_id, cost=calculator.cost(item_id))


if __name__ == "__main__":
    import sys

    if len(sys.argv) < 2:
        print("Usage: python cost_calculator.py <item_id>")
        raise SystemExit(1)

    item_id = int(sys.argv[1])
    result = compute_cost(item_id)
    print(f"Item {result.item_id} cost: {result.cost}")
