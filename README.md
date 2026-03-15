# FFXIV Best Market Crafter

本專案是本地用的 FF14 製作利潤看板，核心功能是：

- 匯入繁中客戶端 `Item.csv` / `Recipe.csv`
- 抓 Universalis 價格
- 用網頁查成品、素材、成本與價差

## 目前正式流程

### 1. 更新食譜與道具資料

先在 Windows 重新解包：

```powershell
cd C:\Users\zing9\Downloads\XivExdUnpacker-win-x64
.\XivExdUnpacker.exe --language tc --sheets Item Recipe --clear
```

再回到 WSL 匯入 SQLite：

```bash
cd /mnt/d/FF\ tools/bestmarketcrafter
source .venv-wsl/bin/activate
python import_tc_exd.py
```

`import_tc_exd.py` 會讀：

- `config.py` 的 `ITEM_CSV_PATH`
- `config.py` 的 `RECIPE_CSV_PATH`

## 價格資料規則

`prices` 表目前會同時存兩種 scope：

- `world='繁中服'`
  - 用來表示區域最低價
  - `world_name` 是這筆最低價實際來自哪個世界
- `world='鳳凰'`
  - 用來表示鳳凰單世界價格

這兩筆不能混成同一筆，因為目前主鍵是：

- `(item_id, world)`

## UI 讀值方式

查詢頁目前規則：

- 成品價格：讀 `鳳凰`
- 素材 `鳳凰價`：讀 `鳳凰`
- 素材 `最低價`：讀 `繁中服`
- 素材 `最低價世界`：讀 `繁中服.world_name`

價差計算：

- `鳳凰成品價格 - 繁中服素材最低成本`

## 利潤排行

`profits` 表是預先計算好的排行來源。

目前重算方式：

- 單張配方更新價格後會重算
- 全量價格更新後也會重算
- 也可以手動跑：

```bash
python update_profits.py
```

## 常用指令

### 啟動 Web UI

```bash
cd /mnt/d/FF\ tools/bestmarketcrafter
source .venv-wsl/bin/activate
python -c 'from web_ui import app; app.run(host="127.0.0.1", port=5000, debug=False)'
```

### 全量更新價格

```bash
cd /mnt/d/FF\ tools/bestmarketcrafter
source .venv-wsl/bin/activate
python update_prices.py
python update_profits.py
```

### 執行測試

```bash
cd /mnt/d/FF\ tools/bestmarketcrafter
source .venv-wsl/bin/activate
python -m unittest tests.test_web_ui
```

## 設定集中位置

主要設定都在 [`config.py`](/mnt/d/FF%20tools/bestmarketcrafter/config.py)：

- `DB_PATH`
- `UNPACKER_DIR`
- `RAWEXD_TC_DIR`
- `ITEM_CSV_PATH`
- `RECIPE_CSV_PATH`
- `LOWEST_WORLD`
- `DISPLAY_WORLD`
- rate limit / batch 參數

## 補充

較偏工作記錄與驗證細節的內容在：

- [`DEV_NOTES.md`](/mnt/d/FF%20tools/bestmarketcrafter/DEV_NOTES.md)
