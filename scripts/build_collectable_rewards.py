from __future__ import annotations

import csv
import sqlite3
from pathlib import Path, PureWindowsPath


REPO_ROOT = Path(__file__).resolve().parents[1]
DB_PATH = REPO_ROOT / "db.sqlite"
OUTPUT_PATH = REPO_ROOT / "data" / "collectable_rewards.csv"
SCRIP_RATE_PATH = REPO_ROOT / "data" / "collectable_scrip_rates.csv"


def resolve_windows_csv(win_path: str) -> Path:
    path = PureWindowsPath(win_path)
    return Path("/mnt") / path.drive[0].lower() / Path(*path.parts[1:])


def load_rate_map(path: Path) -> dict[int, tuple[str, int]]:
    """Load the per-level collectable scrip reward table.

    Each level maps to a single (scrip_type, amount) pair since a given craft
    level only rewards one kind of scrip (e.g. lv91-99 purple, lv100 orange).
    Supports two CSV shapes for backwards compatibility:

      1. New format: ``class_job_level,scrip_type,amount`` (preferred)
      2. Legacy format: ``class_job_level,purple_scrips`` — treated as purple.
    """

    mapping: dict[int, tuple[str, int]] = {}
    with path.open("r", encoding="utf-8", newline="") as handle:
        reader = csv.DictReader(handle)
        for row in reader:
            try:
                level = int((row.get("class_job_level") or "").strip())
            except ValueError:
                continue

            amount_text = (row.get("amount") or row.get("purple_scrips") or "").strip()
            try:
                amount = int(amount_text)
            except ValueError:
                continue

            scrip_type = (row.get("scrip_type") or "purple").strip().lower() or "purple"
            if scrip_type not in {"purple", "orange"}:
                scrip_type = "purple"

            mapping[level] = (scrip_type, amount)
    return mapping


def load_recipe_metadata(recipe_csv: Path, level_csv: Path) -> dict[int, dict[str, str]]:
    recipes: dict[int, dict[str, str]] = {}
    with recipe_csv.open("r", encoding="utf-8-sig", newline="") as handle:
        reader = csv.reader(handle)
        _ = next(reader, None)
        headers = next(reader, None)
        _ = next(reader, None)
        if not headers:
            return recipes
        idx = {name: i for i, name in enumerate(headers) if name}
        for row in reader:
            if not row:
                continue
            try:
                item_id = int((row[idx["ItemResult"]] if idx.get("ItemResult") is not None else "").strip())
            except (ValueError, TypeError, KeyError):
                continue
            recipes[item_id] = {
                "recipe_level_table": row[idx["RecipeLevelTable"]].strip() if idx.get("RecipeLevelTable") is not None and idx["RecipeLevelTable"] < len(row) else "",
                "craft_type": row[idx["CraftType"]].strip() if idx.get("CraftType") is not None and idx["CraftType"] < len(row) else "",
            }

    level_map: dict[str, str] = {}
    with level_csv.open("r", encoding="utf-8-sig", newline="") as handle:
        reader = csv.reader(handle)
        _ = next(reader, None)
        headers = next(reader, None)
        _ = next(reader, None)
        if headers:
            idx = {name: i for i, name in enumerate(headers) if name}
            for row in reader:
                if not row:
                    continue
                key = row[0].strip()
                class_job_level = (
                    row[idx["ClassJobLevel"]].strip()
                    if idx.get("ClassJobLevel") is not None and idx["ClassJobLevel"] < len(row)
                    else ""
                )
                if key:
                    level_map[key] = class_job_level

    result: dict[int, dict[str, str]] = {}
    for item_id, recipe in recipes.items():
        result[item_id] = {
            **recipe,
            "class_job_level": level_map.get(recipe["recipe_level_table"], ""),
        }
    return result


def main() -> int:
    recipe_csv = resolve_windows_csv(r"C:\Users\zing9\Downloads\XivExdUnpacker-win-x64\rawexd\tc\Recipe.csv")
    level_csv = resolve_windows_csv(r"C:\Users\zing9\Downloads\XivExdUnpacker-win-x64\rawexd\tc\RecipeLevelTable.csv")
    rate_map = load_rate_map(SCRIP_RATE_PATH)
    recipe_meta = load_recipe_metadata(recipe_csv, level_csv)

    conn = sqlite3.connect(DB_PATH)
    cur = conn.cursor()
    cur.execute("SELECT item_id, name FROM items WHERE name LIKE '收藏用%' ORDER BY item_id;")
    items = cur.fetchall()
    conn.close()

    OUTPUT_PATH.parent.mkdir(parents=True, exist_ok=True)
    with OUTPUT_PATH.open("w", encoding="utf-8", newline="") as handle:
        writer = csv.writer(handle)
        writer.writerow(
            [
                "item_id",
                "name",
                "purple_scrips",
                "orange_scrips",
                "class_job_level",
                "recipe_level_table",
                "craft_type",
            ]
        )
        for item_id, name in items:
            meta = recipe_meta.get(int(item_id), {})
            class_job_level_text = meta.get("class_job_level", "")
            try:
                class_job_level = int(class_job_level_text)
            except ValueError:
                class_job_level = 0

            # Each level rewards exactly one scrip type. Unknown levels (older
            # obsolete content) fall through to (none, 0) and get filtered out
            # of both cost views instead of showing a misleading stub value.
            scrip_type, amount = rate_map.get(class_job_level, ("none", 0))
            purple_scrips = amount if scrip_type == "purple" else 0
            orange_scrips = amount if scrip_type == "orange" else 0

            writer.writerow(
                [
                    item_id,
                    name,
                    purple_scrips,
                    orange_scrips,
                    class_job_level_text,
                    meta.get("recipe_level_table", ""),
                    meta.get("craft_type", ""),
                ]
            )

    print(f"Wrote {len(items)} rows to {OUTPUT_PATH}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
