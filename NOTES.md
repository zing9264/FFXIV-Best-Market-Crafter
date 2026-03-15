# FF14 Market Crafter Session Notes

## Goal
Build a local FF14 crafting profit dashboard with:
- TW item/recipe data imported into SQLite
- Universalis market data refresh
- A web UI for recipe lookup, ingredient pricing, and margin comparison

## Current Status
Workspace:
`D:\FF tools\bestmarketcrafter`

Current default market scope:
- `FF14_WORLD = 繁中服`

Current DB status:
- `items`: imported from TW `Item.csv`
- `recipes`: imported from TW `Recipe.csv`
- `recipe_ingredients`: imported from TW `Recipe.csv`
- `prices`: partially populated from Universalis

Known validated price sample:
- Item `43335` `相思木指環`
- Scope: `繁中服`
- Stored row currently matches a recent API pull:
  - `world_name = 巴哈姆特`
  - `min_price = 7000`
  - `sale_price = 17495`
  - `listings = 32`
  - `daily_sales = 3.5714285`

## What Was Completed
### Data import
- Re-extracted TW client data with `XivExdUnpacker`
- Re-imported `Item.csv` and `Recipe.csv` into `db.sqlite`
- Verified imported DB counts after refresh

### Market scope correction
- Confirmed previous default `Phoenix` was the wrong target for current use
- Switched project default from `Phoenix` to `繁中服`
- Verified Universalis API accepts:
  - `https://universalis.app/api/v2/繁中服/<item_id>`

### Web UI
- Expanded `web_ui.py` and `templates/index.html`
- Added lookup by:
  - item id
  - item name
- Added recipe detail view with:
  - product price
  - product last sale price
  - product daily sales
  - product listings
  - ingredient unit prices
  - ingredient subtotals
  - material total
  - per-unit material cost
  - price gap
- Added top-100 ranking tab
- Added a button to refresh prices for only:
  - the selected product
  - that product's recipe ingredients

### Price storage
- Extended `prices` schema to include:
  - `sale_price`
  - `world_name`
  - `daily_sales`
  - `last_updated`
- Normalized zero-init behavior in UI:
  - `0` no longer displays as a real product price
  - `0` no longer displays as a real daily sales value

### Universalis parsing
- Current `update_prices.py` stores:
  - `min_price`
  - `listings`
  - `daily_sales`
  - `last_updated`
  - `world_name`
  - `sale_price` from `recentHistory[0].pricePerUnit`
- For aggregate scopes like `繁中服`, `world_name` is derived from:
  - `listings[0].worldName`
  - fallback: `recentHistory[0].worldName`

### Tests
- Added `tests/test_web_ui.py`
- Verified:
  - recipe item expansion
  - refresh route behavior
  - zero-value fallback logic
  - product `sale_price`
  - product `world_name`

### Git
- Added `.gitattributes` to keep source files on LF
- Updated local repo git identity to:
  - `zing9264 <zing9264@gmail.com>`
- Generated a new GitHub SSH key for WSL
- Verified GitHub SSH auth works
- Pushed latest work to:
  - `origin/main`
- Latest pushed commit:
  - `77e2192` `Add TC market scope dashboard refresh and tests`

## Important Findings
### Universalis limits
- REST API limit:
  - `25 req/s`
  - `50 req/s burst`
- Simultaneous connections per IP:
  - `8`

### Product 43335 validation
This item was used to prove the project was querying the wrong market before the switch.

Observed comparison:
- `Phoenix` returned a much higher price
- `繁中服` returned the expected market range

This validated that the discrepancy was caused by target market scope, not only by parsing bugs.

## Known Gaps
- `sale_price` is currently "last observed sale price", not yet "average sale price"
- `p50_price` is still not very meaningful for current Universalis responses and may be removable later
- The UI currently shows `sale_price`, but not yet a separate "current average sale price" field
- Ranking is still based on current stored pricing fields, not a richer sale/listing dual model

## Recommended Next Steps
1. Add `currentAveragePrice` to DB and UI as a separate field.
2. Decide whether ranking should use:
   - listing price
   - last sale price
   - average sale price
3. Add a compact "market source" explanation in the UI for aggregate scopes like `繁中服`.
4. Optionally add a refresh mode for:
   - current product only
   - current recipe only
   - full market scope batch

## Useful Commands
Run WSL web UI:

```bash
cd /mnt/d/FF\ tools/bestmarketcrafter
source .venv-wsl/bin/activate
python -c 'from web_ui import app; app.run(host="127.0.0.1", port=5000, debug=False)'
```

Run tests:

```bash
cd /mnt/d/FF\ tools/bestmarketcrafter
source .venv-wsl/bin/activate
python -m unittest tests.test_web_ui
```

Refresh one product recipe set manually:

```bash
cd /mnt/d/FF\ tools/bestmarketcrafter
source .venv-wsl/bin/activate
python - <<'PY'
import sqlite3
from update_prices import update_prices_for_ids

item_id = 43335
conn = sqlite3.connect("db.sqlite")
cur = conn.cursor()
cur.execute("SELECT ingredient_item_id FROM recipe_ingredients WHERE output_item_id=?", (item_id,))
ids = [item_id] + [row[0] for row in cur.fetchall()]
print(update_prices_for_ids(ids))
PY
```
