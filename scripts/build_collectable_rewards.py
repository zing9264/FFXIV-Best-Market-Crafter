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


def load_rate_map(path: Path) -> dict[int, int]:
    mapping: dict[int, int] = {}
    with path.open("r", encoding="utf-8", newline="") as handle:
        reader = csv.DictReader(handle)
        for row in reader:
            try:
                level = int((row.get("class_job_level") or "").strip())
                scrips = int((row.get("purple_scrips") or "").strip())
            except ValueError:
                continue
            mapping[level] = scrips
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
        writer.writerow(["item_id", "name", "purple_scrips", "class_job_level", "recipe_level_table", "craft_type"])
        for item_id, name in items:
            meta = recipe_meta.get(int(item_id), {})
            class_job_level_text = meta.get("class_job_level", "")
            try:
                class_job_level = int(class_job_level_text)
            except ValueError:
                class_job_level = 0
            purple_scrips = rate_map.get(class_job_level, 45)
            writer.writerow(
                [
                    item_id,
                    name,
                    purple_scrips,
                    class_job_level_text,
                    meta.get("recipe_level_table", ""),
                    meta.get("craft_type", ""),
                ]
            )

    print(f"Wrote {len(items)} rows to {OUTPUT_PATH}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
