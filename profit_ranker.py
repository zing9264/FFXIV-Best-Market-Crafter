from __future__ import annotations

import time

from config import MIN_PROFIT, SELL_PRICE_FIELD, TOP_N, WORLD, MAX_LISTINGS
from cost_calculator import CostCalculator
from db import get_conn, init_db


def get_sell_price(conn, item_id: int):
    cur = conn.cursor()
    cur.execute(
        "SELECT p50_price, min_price, listings FROM prices WHERE item_id=? AND world=?;",
        (item_id, WORLD),
    )
    row = cur.fetchone()
    if not row:
        return None, None
    p50_price, min_price, listings = row
    if SELL_PRICE_FIELD == "min":
        return (min_price if min_price is not None else p50_price), listings
    return (p50_price if p50_price is not None else min_price), listings


def get_item_name(conn, item_id: int):
    cur = conn.cursor()
    cur.execute("SELECT name FROM items WHERE item_id=?;", (item_id,))
    row = cur.fetchone()
    return row[0] if row else None


def rank_profits():
    init_db()
    calculator = CostCalculator(world=WORLD)
    now = int(time.time())

    with get_conn() as conn:
        cur = conn.cursor()
        cur.execute("SELECT output_item_id FROM recipes;")
        output_ids = [row[0] for row in cur.fetchall()]

    results = []
    with get_conn() as conn:
        for item_id in output_ids:
            sell_price, listings = get_sell_price(conn, item_id)
            if sell_price is None:
                continue
            if MAX_LISTINGS > 0 and listings is not None and listings > MAX_LISTINGS:
                continue

            cost = calculator.cost(int(item_id))
            if cost is None:
                continue

            profit = sell_price - cost
            if profit < MIN_PROFIT:
                continue

            name = get_item_name(conn, item_id) or f"Item {item_id}"
            results.append((profit, item_id, name, sell_price, cost, listings))

        cur = conn.cursor()
        cur.executemany(
            "INSERT OR REPLACE INTO profits(item_id, cost, profit, updated) VALUES (?, ?, ?, ?);",
            [(item_id, cost, profit, now) for profit, item_id, _, _, cost, _ in results],
        )

    results.sort(reverse=True, key=lambda x: x[0])
    top = results[:TOP_N]

    print(f"World: {WORLD}")
    print("Top Profitable Crafts:")
    for i, (profit, item_id, name, sell_price, cost, listings) in enumerate(top, start=1):
        listings_txt = f" listings={listings}" if listings is not None else ""
        print(
            f"{i}. {name} (id={item_id}) sell={sell_price:.2f} cost={cost:.2f} "
            f"profit={profit:.2f}{listings_txt}"
        )


if __name__ == "__main__":
    rank_profits()
