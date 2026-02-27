from __future__ import annotations

import json
import sys
import time
import urllib.parse
import urllib.request

from config import XIVAPI_BASE_URL, XIVAPI_KEY
from db import get_conn, init_db


# Recipe fields in XIVAPI are fixed slots 0..9
INGREDIENT_SLOTS = 10


def build_url(page: int, limit: int) -> str:
    columns = [
        "ID",
        "ItemResult",
        "AmountResult",
    ]
    for i in range(INGREDIENT_SLOTS):
        columns.append(f"ItemIngredient{i}")
        columns.append(f"AmountIngredient{i}")

    params = {
        "page": page,
        "limit": limit,
        "columns": ",".join(columns),
    }
    if XIVAPI_KEY:
        params["private_key"] = XIVAPI_KEY

    return f"{XIVAPI_BASE_URL}/recipe?{urllib.parse.urlencode(params)}"


def fetch_json(url: str) -> dict:
    req = urllib.request.Request(
        url,
        headers={
            "User-Agent": "bestmarketcrafter/1.0 (+https://localhost)",
            "Accept": "application/json",
        },
    )
    with urllib.request.urlopen(req) as resp:
        return json.loads(resp.read().decode("utf-8"))


def extract_item_id(value):
    if value is None:
        return None
    if isinstance(value, int):
        return value if value > 0 else None
    if isinstance(value, dict):
        item_id = value.get("ID")
        return item_id if isinstance(item_id, int) and item_id > 0 else None
    return None


def extract_item_name(value):
    if isinstance(value, dict):
        name = value.get("Name")
        return name if isinstance(name, str) and name else None
    return None


def update_recipes(limit: int = 3000):
    init_db()
    page = 1
    total = 0

    with get_conn() as conn:
        cur = conn.cursor()

        while True:
            url = build_url(page, limit)
            data = fetch_json(url)
            results = data.get("Results") or []

            if not results:
                break

            for row in results:
                output_item = row.get("ItemResult")
                output_item_id = extract_item_id(output_item)
                if not output_item_id:
                    continue

                output_name = extract_item_name(output_item)
                if output_name:
                    cur.execute(
                        "INSERT OR REPLACE INTO items(item_id, name) VALUES(?, ?);",
                        (output_item_id, output_name),
                    )

                yield_qty = row.get("AmountResult") or 1
                if not isinstance(yield_qty, int) or yield_qty <= 0:
                    yield_qty = 1

                cur.execute(
                    "INSERT OR REPLACE INTO recipes(output_item_id, yield) VALUES(?, ?);",
                    (output_item_id, yield_qty),
                )

                for i in range(INGREDIENT_SLOTS):
                    ingredient = row.get(f"ItemIngredient{i}")
                    ingredient_id = extract_item_id(ingredient)
                    if not ingredient_id:
                        continue

                    ingredient_name = extract_item_name(ingredient)
                    if ingredient_name:
                        cur.execute(
                            "INSERT OR REPLACE INTO items(item_id, name) VALUES(?, ?);",
                            (ingredient_id, ingredient_name),
                        )

                    qty = row.get(f"AmountIngredient{i}") or 0
                    if not isinstance(qty, int) or qty <= 0:
                        continue

                    cur.execute(
                        """
                        INSERT OR REPLACE INTO recipe_ingredients(
                            output_item_id, ingredient_item_id, qty
                        ) VALUES (?, ?, ?);
                        """,
                        (output_item_id, ingredient_id, qty),
                    )

            total += len(results)
            page += 1

            if page > (data.get("Pagination") or {}).get("PageTotal", page):
                break

            time.sleep(0.2)

    print(f"Updated recipes: {total}")


if __name__ == "__main__":
    try:
        update_recipes()
    except Exception as exc:
        print(f"update_recipes failed: {exc}", file=sys.stderr)
        sys.exit(1)
