# FF14 Market Crafter Session Notes

## Goal
Build a local FF14 crafting profit scanner with:
- Local SQLite DB
- Market prices from Universalis API
- Recipes/ingredients from data source
- Recursive cost calc with memoization
- Profit ranking
- Web UI dashboard to view DB contents

## Current Status (Workspace: `D:\FF tools\bestmarketcrafter`)
### Code added
- `config.py`: config for DB, world, API settings, pricing behavior.
- `db.py`: SQLite schema + init.
- `update_recipes.py`: fetch recipes from XIVAPI v1 (`/recipe`) and write to DB.
- `update_prices.py`: fetch prices from Universalis (batch, rate limited) and write to DB.
- `cost_calculator.py`: recursive cost calculation.
- `profit_ranker.py`: top profitable crafts.
- `web_ui.py` + `templates/index.html`: simple dashboard UI.
- `requirements.txt`: `aiohttp`, `Flask`.
- `scripts/api_smoke_test.py`: v1 XIVAPI recipe smoke test.
- `scripts/v2_lang_probe.py`: v2 XIVAPI language probe (tests `language=cht`).

### Git
- Repo initialized and pushed to GitHub (SSH remote).
- Added `.gitignore` to exclude `__pycache__/` and `*.sqlite`.

## API Findings
### XIVAPI v1
- Recipe endpoint works (sample result confirmed).
- Names are English by default.
- 403 may occur without User-Agent or key.

### XIVAPI v2
- Docs list `language=chs/cht/kr`, but v2 concept docs say only global languages (ja/en/de/fr).
- Need empirical probe to confirm if `language=cht` returns real data.

### Universalis
- Provides market data by `item_id`, not names.
- Has marketable item IDs list.
- No official Chinese name mapping in API docs.

## Local Data Source Discovery
### `D:\FF tools\bestmarketcrafter\src`
Contains `Definitions` and `ffxiv` data packs (`*.dat`, `*.index`) but not extracted CSV.
Not directly usable without extraction.

## Godbert/SaintCoinach
Godbert GUI exists at:
`C:\Users\zing9\Downloads\Godbert`
Godbert closes immediately. Likely missing **.NET 7 Desktop Runtime**.
`Godbert.runtimeconfig.json` indicates:
- `Microsoft.NETCore.App 7.0.0`
- `Microsoft.WindowsDesktop.App 7.0.0`

## Next Steps (Suggested)
1. Export `Item.csv` and `Recipe.csv` from local TW client with `XivExdUnpacker`.
   - `cd C:\Users\zing9\Downloads\XivExdUnpacker-win-x64`
   - `.\XivExdUnpacker.exe --language tc --sheets Item Recipe --clear`
2. Import extracted data into project DB:
   - `python import_tc_exd.py --db "D:\FF tools\bestmarketcrafter\db.sqlite"`
3. Re-run `web_ui.py` to visualize data.

## Notes
- `XivExdUnpacker` may show schema fallback to `latest`; this is expected if exact client version schema is unavailable.
