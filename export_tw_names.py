from __future__ import annotations

import argparse
import csv
import sqlite3
import sys
from pathlib import Path

from item_id_lookup import maybe_get_opencc_converter


DEFAULT_SOURCE_DB = r"D:\FF tools\assets\xiv.db"
DEFAULT_OUT_CSV = "item_names_zh_tw.csv"


def configure_stdout_utf8() -> None:
    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(encoding="utf-8")


def export_names(source_db: str, out_csv: str, s2t: bool) -> int:
    converter = maybe_get_opencc_converter(s2t)

    db_path = Path(source_db)
    if not db_path.exists():
        raise FileNotFoundError(f"Database not found: {db_path}")

    out_path = Path(out_csv)
    if not out_path.is_absolute():
        out_path = Path.cwd() / out_path

    count = 0
    with sqlite3.connect(str(db_path)) as conn, out_path.open("w", newline="", encoding="utf-8-sig") as f:
        cur = conn.cursor()
        cur.execute(
            """
            SELECT Id, Name
            FROM Items
            WHERE Name IS NOT NULL
              AND Name <> ''
            ORDER BY Id;
            """
        )

        writer = csv.writer(f)
        writer.writerow(["item_id", "name_zh_cn", "name_zh_tw"])
        for item_id, name_cn in cur.fetchall():
            name_tw = converter.convert(name_cn) if converter else name_cn
            writer.writerow([item_id, name_cn, name_tw])
            count += 1
    return count


def main() -> int:
    configure_stdout_utf8()

    parser = argparse.ArgumentParser(description="Export FF14 item names as zh-TW mapping CSV.")
    parser.add_argument("--source-db", default=DEFAULT_SOURCE_DB, help=f"Path to xiv.db (default: {DEFAULT_SOURCE_DB})")
    parser.add_argument("--out", default=DEFAULT_OUT_CSV, help=f"Output CSV path (default: {DEFAULT_OUT_CSV})")
    parser.add_argument("--no-s2t", action="store_true", help="Do not convert to traditional; keep zh_cn in zh_tw column.")
    args = parser.parse_args()

    try:
        count = export_names(args.source_db, args.out, s2t=not args.no_s2t)
        print(f"Exported {count} rows -> {args.out}")
        return 0
    except (FileNotFoundError, RuntimeError) as exc:
        print(str(exc), file=sys.stderr)
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
