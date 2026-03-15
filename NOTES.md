# FF14 Market Crafter Session Notes

## Goal
Build a local FF14 crafting profit dashboard with:
- TW item/recipe data imported into SQLite
- Universalis market data refresh
- A web UI for recipe lookup, ingredient pricing, and profit comparison

## Current Data Rules
### Core pricing model
`prices` now stores two separate Universalis scopes for the same item:

- `world='繁中服'`
  - aggregate scope
  - used for lowest price across the region
  - `world_name` is the actual cheapest source world for that row
- `world='鳳凰'`
  - single-world scope
  - used for explicit Phoenix price display
  - `world_name` is usually `鳳凰`

This is intentional.

Do not try to store Phoenix pulls as:
- `world='繁中服'`
- `world_name='鳳凰'`

under the current schema, because `prices` uses primary key:
- `(item_id, world)`

If both aggregate and Phoenix data used `world='繁中服'`, one row would overwrite the other.

## Why Two Scopes Are Required
Using only `繁中服` is not enough for the UI requirement.

For aggregate scope responses:
- `min_price` tells us the cheapest price in the region
- `world_name` tells us which world that cheapest price came from

But this does **not** tell us:
- what the Phoenix price is for the same item

Example:
- a material may have
  - `繁中服 lowest = 15`
  - `lowest world = 伊弗利特`
- while Phoenix may be
  - `鳳凰 price = 23`

So if the UI needs both:
- Phoenix price
- regional lowest price

then we must fetch and store:
- `繁中服`
- `鳳凰`

separately.

## Current UI Read Rules
### Product display
- product price: read from `prices.world='鳳凰'`
- product last sale price: read from `prices.world='鳳凰'`
- product daily sales: read from `prices.world='鳳凰'`

### Ingredient display
- `最低價`: read from `prices.world='繁中服'`
- `最低價世界`: read from `prices.world='繁中服'.world_name`
- `鳳凰價`: read from `prices.world='鳳凰'`

### Cost calculation
- material line total uses `繁中服` lowest price
- total material cost uses `繁中服` lowest price
- price gap uses:
  - `鳳凰 product price`
  - minus `繁中服 material cost`

This is deliberate:
- selling assumption = Phoenix
- shopping assumption = best price within TC region

## Current Profit Storage
`profits` is now the precomputed ranking source.

It stores at least:
- `item_id`
- `world`
- `world_name`
- `listing_price`
- `sale_price`
- `material_total`
- `unit_material_cost`
- `profit_by_listing`
- `profit_by_sale`
- `daily_sales`
- `updated`

Current ranking world:
- `world='鳳凰'`

Meaning:
- ranking compares Phoenix product price
- against TC regional lowest material cost

## update_prices.py Rules
Running `update_prices.py` now means:

1. fetch all relevant items for `繁中服`
2. fetch all relevant items for `鳳凰`
3. normalize both into the same `prices` schema

### Real API normalization notes
Validated with actual Universalis responses:

- `繁中服`
  - top-level `worldName` may be missing
  - fallback is needed from:
    - `listings[0].worldName`
    - then `recentHistory[0].worldName`
- `鳳凰`
  - top-level `worldName` is present

Current normalization in `build_price_row()`:
- `world_name` priority:
  - top-level `worldName`
  - listings first row `worldName`
  - recentHistory first row `worldName`

Other stored fields:
- `min_price`
- `sale_price`
- `listings`
- `daily_sales`
- `last_updated`

## Web UI Buttons
### Single recipe refresh
The lookup page button:
- updates the selected product
- updates that product's recipe ingredients
- fetches both:
  - `繁中服`
  - `鳳凰`
- then rebuilds `profits`

### Full refresh
The main dashboard now has a full-refresh control.

Expected behavior:
- fetch all prices for `繁中服`
- fetch all prices for `鳳凰`
- rebuild `profits`
- expose status in `/refresh-status`

Progress panel shows:
- current scope
- completed batches / total batches
- updated row count
- rebuilt profit count

## Current Caveat
If only a few Phoenix rows exist, ranking will look "broken" even when the page itself is fine.

That is because ranking now depends on:
- product rows existing in `world='鳳凰'`

If `鳳凰` is incomplete:
- lookup page may still partly work
- ranking coverage will be very small

So when ranking suddenly becomes tiny, the first thing to check is:
- whether `鳳凰` prices have been fully refreshed

## Verified Example
### Item 41618 `翠光騎士武具`
This item was used to verify the dual-scope fix.

Before dual-scope refresh:
- some ingredients had `繁中服` lowest price
- but no Phoenix price

After refreshing both scopes:
- missing Phoenix ingredient prices appeared correctly

Examples observed after refresh:
- `半魔晶石壹型`
  - `繁中服 lowest = 444`
  - `lowest world = 巴哈姆特`
  - `鳳凰 = 800`
- `火之晶簇`
  - `繁中服 lowest = 28`
  - `鳳凰 = 35`
- `矮人銀錠`
  - `繁中服 lowest = 120`
  - `鳳凰 = 690`

This confirmed the data model issue was real:
- aggregate-only data cannot provide explicit Phoenix price

## Useful Commands
Run web UI in WSL:

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

Run full market refresh from CLI:

```bash
cd /mnt/d/FF\ tools/bestmarketcrafter
source .venv-wsl/bin/activate
python update_prices.py
python update_profits.py
```

Refresh one recipe set manually for both scopes:

```bash
cd /mnt/d/FF\ tools/bestmarketcrafter
source .venv-wsl/bin/activate
python - <<'PY'
import sqlite3
from update_prices import update_prices_for_worlds

item_id = 41618
conn = sqlite3.connect("db.sqlite")
cur = conn.cursor()
cur.execute("SELECT ingredient_item_id FROM recipe_ingredients WHERE output_item_id=?", (item_id,))
ids = [item_id] + [row[0] for row in cur.fetchall()]
print(update_prices_for_worlds(ids, ["繁中服", "鳳凰"]))
PY
python update_profits.py
```
