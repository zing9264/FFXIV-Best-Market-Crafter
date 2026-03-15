from __future__ import annotations

import argparse
import csv
import sqlite3
import sys
from pathlib import Path
from typing import Dict, Iterable, List, Tuple

from config import DB_PATH, ITEM_CSV_PATH, RECIPE_CSV_PATH
from db import init_db


def parse_int(value: str) -> int | None:
    if value is None:
        return None
    text = str(value).strip()
    if not text:
        return None
    try:
        num = int(text)
    except ValueError:
        return None
    return num


def load_exd_csv(path: str) -> Iterable[Dict[str, str]]:
    with open(path, "r", encoding="utf-8-sig", newline="") as f:
        reader = csv.reader(f)
        keys = next(reader, None)  # key,0,1,2...
        headers = next(reader, None)  # #,FieldA,FieldB...
        _ = next(reader, None)  # offset,...

        if not headers or not keys:
            return

        for row in reader:
            if not row:
                continue
            record: Dict[str, str] = {}
            # The first line contains stable source keys like "key".
            if len(row) > 0 and len(keys) > 0 and keys[0]:
                record[keys[0]] = row[0]
            for idx, h in enumerate(headers):
                if not h:
                    continue
                record[h] = row[idx] if idx < len(row) else ""
            yield record


def read_items(item_csv_path: str) -> List[Tuple[int, str]]:
    rows: List[Tuple[int, str]] = []
    for rec in load_exd_csv(item_csv_path):
        item_id = parse_int(rec.get("key", ""))
        if not item_id:
            continue
        name = (rec.get("Name") or "").strip()
        if not name:
            continue
        rows.append((item_id, name))
    return rows


def read_recipes(recipe_csv_path: str) -> Tuple[List[Tuple[int, int]], List[Tuple[int, int, int]]]:
    recipes: List[Tuple[int, int]] = []
    ingredients: List[Tuple[int, int, int]] = []

    for rec in load_exd_csv(recipe_csv_path):
        output_item_id = parse_int(rec.get("ItemResult", ""))
        if not output_item_id:
            continue

        amount_result = parse_int(rec.get("AmountResult", "")) or 1
        if amount_result <= 0:
            amount_result = 1
        recipes.append((output_item_id, amount_result))

        for i in range(8):
            ing_id = parse_int(rec.get(f"Ingredient[{i}]", ""))
            qty = parse_int(rec.get(f"AmountIngredient[{i}]", ""))
            if not ing_id or not qty or qty <= 0:
                continue
            ingredients.append((output_item_id, ing_id, qty))

    return recipes, ingredients


def import_into_db(
    db_path: str,
    items: List[Tuple[int, str]],
    recipes: List[Tuple[int, int]],
    ingredients: List[Tuple[int, int, int]],
    clear_items: bool,
) -> None:
    init_db()
    conn = sqlite3.connect(db_path)
    try:
        cur = conn.cursor()

        # Replace recipes to avoid mixing API and unpacked sources.
        cur.execute("DELETE FROM recipe_ingredients;")
        cur.execute("DELETE FROM recipes;")
        if clear_items:
            cur.execute("DELETE FROM items;")

        cur.executemany(
            "INSERT OR REPLACE INTO items(item_id, name) VALUES(?, ?);",
            items,
        )
        cur.executemany(
            "INSERT OR REPLACE INTO recipes(output_item_id, yield) VALUES(?, ?);",
            recipes,
        )
        cur.executemany(
            """
            INSERT OR REPLACE INTO recipe_ingredients(
                output_item_id, ingredient_item_id, qty
            ) VALUES (?, ?, ?);
            """,
            ingredients,
        )

        conn.commit()
    finally:
        conn.close()


def main() -> int:
    parser = argparse.ArgumentParser(description="Import TC Item/Recipe CSV exported by XivExdUnpacker.")
    parser.add_argument("--item-csv", default=ITEM_CSV_PATH, help=f"Path to Item.csv (default: {ITEM_CSV_PATH})")
    parser.add_argument(
        "--recipe-csv",
        default=RECIPE_CSV_PATH,
        help=f"Path to Recipe.csv (default: {RECIPE_CSV_PATH})",
    )
    parser.add_argument("--db", default=DB_PATH, help=f"Path to target SQLite DB (default: {DB_PATH})")
    parser.add_argument(
        "--keep-existing-items",
        action="store_true",
        help="Do not clear items table before import (default clears and rebuilds names from Item.csv).",
    )
    args = parser.parse_args()

    item_csv = Path(args.item_csv)
    recipe_csv = Path(args.recipe_csv)
    if not item_csv.exists():
        print(f"Item CSV not found: {item_csv}", file=sys.stderr)
        return 2
    if not recipe_csv.exists():
        print(f"Recipe CSV not found: {recipe_csv}", file=sys.stderr)
        return 2

    items = read_items(str(item_csv))
    recipes, ingredients = read_recipes(str(recipe_csv))

    import_into_db(
        db_path=args.db,
        items=items,
        recipes=recipes,
        ingredients=ingredients,
        clear_items=not args.keep_existing_items,
    )

    print(f"Imported items: {len(items)}")
    print(f"Imported recipes: {len(recipes)}")
    print(f"Imported recipe ingredients: {len(ingredients)}")
    print(f"Database updated: {args.db}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
