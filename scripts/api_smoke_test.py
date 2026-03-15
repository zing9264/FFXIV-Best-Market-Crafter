from __future__ import annotations

import json
import os
import sys
import urllib.parse
import urllib.request


XIVAPI_BASE_URL = os.environ.get("XIVAPI_BASE_URL", "https://xivapi.com")
XIVAPI_KEY = os.environ.get("XIVAPI_KEY")


def build_url():
    params = {
        "limit": 1,
        "page": 1,
        "columns": ",".join(
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
    with urllib.request.urlopen(req, timeout=20) as resp:
        return json.loads(resp.read().decode("utf-8"))


def main():
    url = build_url()
    print(f"GET {url}")
    data = fetch_json(url)

    pagination = data.get("Pagination") or data.get("pagination")
    results = data.get("Results") or data.get("results") or []

    print("Pagination:", pagination)
    print("Results length:", len(results))
    if results:
        print("Sample result keys:", list(results[0].keys()))
        print("Sample result:", json.dumps(results[0], ensure_ascii=False, indent=2))


if __name__ == "__main__":
    try:
        main()
    except Exception as exc:
        print(f"Smoke test failed: {exc}", file=sys.stderr)
        sys.exit(1)
