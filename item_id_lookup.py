from __future__ import annotations

import argparse
import sqlite3
import sys
import ctypes
from pathlib import Path
from typing import Iterable, List, Sequence, Tuple

from config import DB_PATH
from db import init_db


DEFAULT_SOURCE_DB = r"D:\FF tools\assets\xiv.db"


def configure_stdout_utf8() -> None:
    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(encoding="utf-8")


def maybe_get_opencc_converter(enabled: bool):
    if not enabled:
        return None

    # Built-in Windows conversion first (no external dependency needed).
    if sys.platform.startswith("win"):
        try:
            return WindowsS2TConverter()
        except Exception:
            pass

    try:
        from opencc import OpenCC  # type: ignore
    except Exception as exc:  # pragma: no cover - runtime dependency check
        raise RuntimeError(
            "Traditional conversion requested, but no converter is available. "
            "On Windows, this should work without extra install. "
            "If it still fails, install OpenCC: pip install opencc-python-reimplemented"
        ) from exc
    return OpenCC("s2t")


class WindowsS2TConverter:
    # Win32 LCMAP_TRADITIONAL_CHINESE
    LCMAP_TRADITIONAL_CHINESE = 0x04000000
    LOCALE_ZH_TW = 0x0404

    def __init__(self) -> None:
        kernel32 = ctypes.windll.kernel32
        self._lcmap = kernel32.LCMapStringW
        self._lcmap.argtypes = [
            ctypes.c_uint32,  # Locale
            ctypes.c_uint32,  # dwMapFlags
            ctypes.c_wchar_p,  # lpSrcStr
            ctypes.c_int,  # cchSrc
            ctypes.c_wchar_p,  # lpDestStr
            ctypes.c_int,  # cchDest
        ]
        self._lcmap.restype = ctypes.c_int

    def convert(self, text: str) -> str:
        if not text:
            return text
        needed = self._lcmap(
            self.LOCALE_ZH_TW,
            self.LCMAP_TRADITIONAL_CHINESE,
            text,
            len(text),
            None,
            0,
        )
        if needed <= 0:
            return text
        buf = ctypes.create_unicode_buffer(needed)
        out = self._lcmap(
            self.LOCALE_ZH_TW,
            self.LCMAP_TRADITIONAL_CHINESE,
            text,
            len(text),
            buf,
            needed,
        )
        if out <= 0:
            return text
        return buf.value


def convert_name(name: str, converter) -> str:
    if not converter:
        return name
    return converter.convert(name)


def chunks(seq: Sequence[int], size: int) -> Iterable[Sequence[int]]:
    for i in range(0, len(seq), size):
        yield seq[i : i + size]


def get_conn(path: str) -> sqlite3.Connection:
    p = Path(path)
    if not p.exists():
        raise FileNotFoundError(f"Database not found: {p}")
    return sqlite3.connect(str(p))


def find_by_name(source_db: str, keyword: str, limit: int, convert_s2t: bool) -> List[Tuple[int, str]]:
    converter = maybe_get_opencc_converter(convert_s2t)

    with get_conn(source_db) as conn:
        cur = conn.cursor()

        # Exact match first, then partial match.
        cur.execute(
            """
            SELECT Id, Name
            FROM Items
            WHERE Name = ?
            ORDER BY Id
            LIMIT ?;
            """,
            (keyword, limit),
        )
        exact = [(item_id, convert_name(name, converter)) for item_id, name in cur.fetchall()]
        if exact:
            return exact

        cur.execute(
            """
            SELECT Id, Name
            FROM Items
            WHERE Name LIKE ?
            ORDER BY Id
            LIMIT ?;
            """,
            (f"%{keyword}%", limit),
        )
        return [(item_id, convert_name(name, converter)) for item_id, name in cur.fetchall()]


def find_by_id(source_db: str, item_id: int, convert_s2t: bool) -> Tuple[int, str] | None:
    converter = maybe_get_opencc_converter(convert_s2t)
    with get_conn(source_db) as conn:
        cur = conn.cursor()
        cur.execute("SELECT Id, Name FROM Items WHERE Id = ?;", (item_id,))
        row = cur.fetchone()
        if not row:
            return None
        return (row[0], convert_name(row[1], converter))


def get_target_item_ids(target_db: str) -> List[int]:
    with get_conn(target_db) as conn:
        cur = conn.cursor()
        cur.execute(
            """
            SELECT item_id FROM items
            UNION
            SELECT output_item_id FROM recipes
            UNION
            SELECT ingredient_item_id FROM recipe_ingredients
            UNION
            SELECT item_id FROM prices
            """
        )
        rows = cur.fetchall()
    return sorted({r[0] for r in rows if isinstance(r[0], int) and r[0] > 0})


def fetch_names_by_ids(source_db: str, ids: List[int], convert_s2t: bool) -> List[Tuple[int, str]]:
    converter = maybe_get_opencc_converter(convert_s2t)
    if not ids:
        return []

    rows: List[Tuple[int, str]] = []
    with get_conn(source_db) as conn:
        cur = conn.cursor()
        for part in chunks(ids, 900):
            placeholders = ",".join("?" for _ in part)
            cur.execute(
                f"""
                SELECT Id, Name
                FROM Items
                WHERE Id IN ({placeholders})
                  AND Name IS NOT NULL
                  AND Name <> '';
                """,
                list(part),
            )
            rows.extend((item_id, convert_name(name, converter)) for item_id, name in cur.fetchall())
    return rows


def sync_names(source_db: str, target_db: str, all_items: bool, convert_s2t: bool) -> int:
    converter = maybe_get_opencc_converter(convert_s2t)
    init_db()

    if all_items:
        with get_conn(source_db) as conn:
            cur = conn.cursor()
            cur.execute(
                """
                SELECT Id, Name
                FROM Items
                WHERE Name IS NOT NULL
                  AND Name <> '';
                """
            )
            rows = [(item_id, convert_name(name, converter)) for item_id, name in cur.fetchall()]
    else:
        ids = get_target_item_ids(target_db)
        rows = fetch_names_by_ids(source_db, ids, convert_s2t)

    with get_conn(target_db) as conn:
        cur = conn.cursor()
        cur.executemany(
            "INSERT OR REPLACE INTO items(item_id, name) VALUES(?, ?);",
            rows,
        )
        conn.commit()
    return len(rows)


def main() -> int:
    configure_stdout_utf8()

    parser = argparse.ArgumentParser(
        description="Lookup or sync FF14 item name <-> ID from local xiv.db."
    )
    parser.add_argument(
        "--source-db",
        default=DEFAULT_SOURCE_DB,
        help=f"Path to source xiv.db (default: {DEFAULT_SOURCE_DB})",
    )
    parser.add_argument(
        "--target-db",
        default=DB_PATH,
        help=f"Path to project DB (default: {DB_PATH})",
    )

    sub = parser.add_subparsers(dest="cmd", required=True)

    p_find = sub.add_parser("find", help="Find item IDs by name (exact or partial).")
    p_find.add_argument("name", help="Item name keyword (e.g. 星極水鏡)")
    p_find.add_argument("--limit", type=int, default=20)
    p_find.add_argument("--s2t", action="store_true", help="Convert simplified names to traditional for output.")

    p_id = sub.add_parser("id", help="Find item name by ID.")
    p_id.add_argument("item_id", type=int)
    p_id.add_argument("--s2t", action="store_true", help="Convert simplified name to traditional for output.")

    p_sync = sub.add_parser("sync", help="Sync names into project DB items table.")
    p_sync.add_argument(
        "--all",
        action="store_true",
        help="Import all item IDs from source DB, not only IDs already used in project.",
    )
    p_sync.add_argument(
        "--s2t",
        action="store_true",
        help="Convert simplified names to traditional before writing to project DB.",
    )

    args = parser.parse_args()

    try:
        if args.cmd == "find":
            rows = find_by_name(args.source_db, args.name, args.limit, args.s2t)
            if not rows:
                print("No matches.")
                return 1
            for item_id, name in rows:
                print(f"{item_id}\t{name}")
            return 0

        if args.cmd == "id":
            row = find_by_id(args.source_db, args.item_id, args.s2t)
            if not row:
                print("Not found.")
                return 1
            print(f"{row[0]}\t{row[1]}")
            return 0

        if args.cmd == "sync":
            count = sync_names(args.source_db, args.target_db, args.all, args.s2t)
            scope = "all source items" if args.all else "project-used item IDs"
            print(f"Synced {count} names ({scope}).")
            return 0

        parser.print_help()
        return 1
    except FileNotFoundError as exc:
        print(str(exc), file=sys.stderr)
        return 2
    except RuntimeError as exc:
        print(str(exc), file=sys.stderr)
        return 3


if __name__ == "__main__":
    raise SystemExit(main())
