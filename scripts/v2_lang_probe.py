from __future__ import annotations

import json
import os
import sys
import urllib.parse
import urllib.request


BASE_URL = os.environ.get("XIVAPI_V2_BASE_URL", "https://v2.xivapi.com/api")
LANG = os.environ.get("XIVAPI_LANG", "en")  # try: en, ja, de, fr, chs, cht, kr
ROW_ID = os.environ.get("XIVAPI_ROW_ID", "1")  # Recipe row id to probe


def build_url():
    params = {
        "rows": ROW_ID,
        "language": LANG,
        "fields": ",".join(
            [
                "ID",
                "ItemResult.ID",
                "ItemResult.Name",
                "AmountResult",
                "ItemIngredient0.ID",
                "ItemIngredient0.Name",
                "AmountIngredient0",
            ]
        ),
    }
    return f"{BASE_URL}/sheet/Recipe?{urllib.parse.urlencode(params)}"


def fetch_json(url: str) -> dict:
    req = urllib.request.Request(
        url,
        headers={
            "User-Agent": "bestmarketcrafter/1.0 (+https://localhost)",
            "Accept": "application/json",
        },
    )
    with urllib.request.urlopen(req, timeout=20) as resp:
        return json.loads(resp.read().decode("utf-8"))


def main():
    url = build_url()
    print(f"GET {url}")
    data = fetch_json(url)
    print(json.dumps(data, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    try:
        main()
    except Exception as exc:
        print(f"v2 probe failed: {exc}", file=sys.stderr)
        sys.exit(1)
