from __future__ import annotations

import argparse
import csv
from pathlib import Path

from config import COLLECTABLE_REWARDS_CSV_PATH, DB_PATH
from db import get_conn, init_db


DEFAULT_CSV_PATH = COLLECTABLE_REWARDS_CSV_PATH


def parse_int(value: str, default: int = 0) -> int:
    try:
        return int(str(value).strip())
    except (TypeError, ValueError):
        return default


def import_collectable_rewards(csv_path: str = DEFAULT_CSV_PATH) -> int:
    path = Path(csv_path)
    if not path.exists():
        return 0

    rows: list[tuple[int, int, int, int, int, int]] = []
    with path.open("r", encoding="utf-8", newline="") as handle:
        reader = csv.DictReader(handle)
        for row in reader:
            item_id = parse_int(row.get("item_id"), default=0)
            if item_id <= 0:
                continue
            rows.append(
                (
                    item_id,
                    # Default to 0 rather than the old 45 stub: unknown or
                    # obsolete levels shouldn't pretend to reward purple scrips.
                    parse_int(row.get("purple_scrips"), default=0),
                    parse_int(row.get("orange_scrips"), default=0),
                    parse_int(row.get("class_job_level"), default=0),
                    parse_int(row.get("recipe_level_table"), default=0),
                    parse_int(row.get("craft_type"), default=-1),
                )
            )

    init_db()
    with get_conn() as conn:
        cur = conn.cursor()
        cur.execute("DELETE FROM collectable_rewards;")
        cur.executemany(
            """
            INSERT OR REPLACE INTO collectable_rewards(
                item_id, purple_scrips, orange_scrips,
                class_job_level, recipe_level_table, craft_type
            ) VALUES (?, ?, ?, ?, ?, ?);
            """,
            rows,
        )
        return len(rows)


def main() -> int:
    parser = argparse.ArgumentParser(description="Import collectable reward mapping CSV into SQLite.")
    parser.add_argument("--csv", default=DEFAULT_CSV_PATH, help=f"Path to collectable rewards CSV (default: {DEFAULT_CSV_PATH})")
    parser.add_argument("--db", default=DB_PATH, help=f"Unused compatibility arg; DB path is configured in config.py (default: {DB_PATH})")
    args = parser.parse_args()
    imported = import_collectable_rewards(args.csv)
    print(f"Imported collectable rewards: {imported}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
