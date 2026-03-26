# FF14 Market Crafter Developer Manual

本文件是開發者手冊，重點不是使用教學，而是記錄目前系統的設計邏輯、資料口徑、頁面職責，以及實作落點。

## 1. 系統總覽

目前專案分成 4 條主流程：

1. 匯入遊戲資料
   - `import_tc_exd.py`
   - 讀 `Item.csv` / `Recipe.csv`
   - 寫入 `items`、`recipes`、`recipe_ingredients`

2. 抓市場價格
   - `update_prices.py`
   - 抓 Universalis `繁中服` 與 `鳳凰`
   - 寫入 `prices`

3. 重算利潤
   - `update_profits.py`
   - 讀 `prices` 與配方
   - 預先算好排行用資料
   - 寫入 `profits`

4. Web UI
   - `web_ui.py`
   - `templates/index.html`
   - 負責展示、觸發更新、下載 log、查詢排行

## 2. 正式資料更新流程

### 2.1 遊戲資料更新

Windows 端先重新解包：

```powershell
cd C:\Users\zing9\Downloads\XivExdUnpacker-win-x64
.\XivExdUnpacker.exe --language tc --sheets Item Recipe RecipeLevelTable --clear
```

WSL 端再匯入：

```bash
cd /mnt/d/FF\ tools/bestmarketcrafter
source .venv-wsl/bin/activate
python import_tc_exd.py
```

這條流程的落點：
- 設定來源在 `config.py`
- 匯入程式在 `import_tc_exd.py`

### 2.2 市場價格更新

正式口徑不是只抓一個 scope，而是兩個：

- `LOWEST_WORLD = 繁中服`
- `DISPLAY_WORLD = 鳳凰`

更新指令：

```bash
python update_prices.py
python update_profits.py
```

或從網頁按：
- `開始更新所有價格`

這條流程的落點：
- 主要邏輯：`update_prices.py`
- 全量更新背景執行：`web_ui.py / run_full_refresh_job()`
- 重算利潤：`update_profits.py / rebuild_profits()`

## 3. 核心資料表

### 3.1 `prices`

用途：
- 存原始市場資料
- 一個 item 可同時有 `繁中服` 與 `鳳凰` 兩筆

主鍵：
- `(item_id, world)`

主要欄位：
- `item_id`
- `world`
- `world_name`
- `min_price`
- `sale_price`
- `listings`
- `daily_sales`
- `last_updated`

設計重點：
- `world='繁中服'` 表示區域最低價 scope
- `world='鳳凰'` 表示鳳凰單世界價格
- 這兩筆不能混寫成同一筆

實作位置：
- schema：`db.py / init_db()`
- 寫入：`update_prices.py / persist_rows()`

### 3.2 `profits`

用途：
- 存排行與利潤頁面使用的預先計算結果
- 避免每次開排行頁都即時計算整張表

目前 `world` 欄位的語意：
- 不是成品世界
- 而是「素材成本口徑」

目前會有兩種 world：
- `繁中服`
- `鳳凰`

代表：
- 同一個成品，會預先算兩份成本口徑
- 成品價格仍固定讀 `DISPLAY_WORLD = 鳳凰`

主要欄位：
- `listing_price`
- `sale_price`
- `material_total`
- `unit_material_cost`
- `display_unit_material_cost`
- `profit_by_listing`
- `profit_by_sale`
- `profit_margin_pct`
- `sale_margin_pct`
- `daily_sales`

欄位語意：
- `unit_material_cost`
  - 目前選定口徑的單件素材成本
  - world=繁中服 時就是全伺服器最低成本
  - world=鳳凰 時就是鳳凰成本
- `display_unit_material_cost`
  - 固定存鳳凰單件成本
  - 用來在排行頁額外顯示 `單件成本(鳳凰)`

實作位置：
- schema：`db.py / init_db()`
- 重算：`update_profits.py / rebuild_profits()`

### 3.3 `collectable_rewards`

用途：
- 收藏品票數與職業/等級的本地對照表

來源：
- `data/collectable_rewards.csv`
- `import_collectable_rewards.py`

主要欄位：
- `item_id`
- `purple_scrips`
- `class_job_level`
- `recipe_level_table`
- `craft_type`

## 4. 價格口徑規則

這是目前整個系統最重要的設計。

### 4.1 成品價格口徑

成品價格固定看：
- `DISPLAY_WORLD = 鳳凰`

原因：
- 使用者實際要賣的世界是鳳凰
- 排行與收藏品比較都以鳳凰售價為基準

### 4.2 素材成本口徑

素材成本現在可切換兩種：

- `全伺服器`
  - 實際讀 `world='繁中服'`
  - 代表繁中服區域最低成本
- `鳳凰`
  - 實際讀 `world='鳳凰'`
  - 代表若只在鳳凰買材料的成本

這個切換會影響：
- 排行頁的 `單件素材成本`
- 當前價差
- 當前獲利%
- 過去獲利
- 過去獲利%
- 收藏品頁的 `單件成本`
- 收藏品頁的 `每張紫票成本`

不會影響：
- 成品價格
- 成品上次成交價
- 成品成交筆數

### 4.3 為什麼固定再多顯示一欄 `單件成本(鳳凰)`

因為使用者想同時知道：
- 用目前所選口徑算出來的成本
- 與鳳凰實際成本相比差多少

所以現在：
- 排行頁會同時顯示
  - `單件素材成本(目前口徑)`
  - `單件成本(鳳凰)`
- 收藏品頁也同樣顯示

## 5. 頁面設計邏輯

整個網站目前都在同一個 route：
- `web_ui.py / index()`
- `templates/index.html`

切頁靠 query param：
- `tab=lookup`
- `tab=ranking`
- `tab=collectables`

### 5.1 首頁/查詢頁 `tab=lookup`

用途：
- 單品查詢
- 查看成品價格、材料明細、價差
- 針對單一配方即時更新價格

資料來源：
- 搜尋結果：`web_ui.py / search_items()`
- 單品明細：`web_ui.py / load_recipe_detail()`

設計邏輯：
- 成品價格固定讀鳳凰
- 素材表同時顯示：
  - `鳳凰價`
  - `最低價`
  - `最低價世界`
- 單張配方更新時只更新：
  - 該成品
  - 該配方所有素材

按鈕：
- `更新這張製作表價格`
  - route：`web_ui.py / refresh_recipe_prices()`
  - 會抓 `繁中服 + 鳳凰`
  - 然後重算 `profits`

模板位置：
- `templates/index.html`
- `tab == "lookup"` 那一段

### 5.2 排行頁 `tab=ranking`

用途：
- 顯示預先算好的利潤排行
- 提供排序、過濾、分頁

資料來源：
- `web_ui.py / get_profit_count()`
- `web_ui.py / get_top_profit_rows()`
- 背後讀的是 `profits`

設計邏輯：
- 不在頁面臨時計算利潤
- 利潤要先由 `update_profits.py` 預先算入 DB
- 切換 `物價口徑` 時，只是切 `profits.world`

目前支援排序：
- `當前價差`
- `當前獲利%`
- `過去獲利`
- `過去獲利%`

目前支援過濾：
- `最低近三天成交筆數`
- `最低價差`
- `最低過去獲利`
- `最低當前獲利%`
- `最低過去獲利%`
- `最低成品價格`
- `物價口徑`

分頁：
- 每頁 `100` 筆
- page query param 由 `load_dashboard_data()` 處理

按鈕：
- `重算總價差`
  - route：`web_ui.py / refresh_profits()`
  - 直接跑 `rebuild_profits()`

模板位置：
- `templates/index.html`
- `tab == "ranking"` 那一段

### 5.3 收藏品成本頁 `tab=collectables`

用途：
- 顯示收藏品的職業、等級、紫票
- 計算單件成本與每張紫票成本

資料來源：
- `web_ui.py / get_collectable_rows()`
- 讀：
  - `collectable_rewards`
  - `recipes`
  - `recipe_ingredients`
  - `prices`

設計邏輯：
- 支援 `全伺服器 / 鳳凰` 物價口徑
- 每張紫票成本 = `單件成本 / purple_scrips`
- 固定再顯示一欄 `單件成本(鳳凰)` 作為對照

排序：
- `每張紫票成本高到低`
- `每張紫票成本低到高`

模板位置：
- `templates/index.html`
- `tab == "collectables"` 那一段

### 5.4 最近價格更新區塊

用途：
- 顯示最近寫進 `prices` 的資料
- 快速檢查市場更新是否成功

資料來源：
- `web_ui.py / get_latest_prices()`

內容：
- item 名稱
- 來源世界
- 最低價
- 上次成交價
- 近三天成交筆數
- 掛單數
- 更新時間

模板位置：
- `templates/index.html`
- 頁面最下方 `最近價格更新`

## 6. 全量更新與進度面板

### 6.1 全量更新

route：
- `web_ui.py / refresh_all_prices()`

背景工作：
- `web_ui.py / start_full_refresh_job()`
- `web_ui.py / run_full_refresh_job()`

執行順序：
1. 更新 `繁中服`
2. 更新 `鳳凰`
3. 重算 `profits`

### 6.2 中斷更新

route：
- `web_ui.py / cancel_refresh()`

方式：
- 只設定 `cancel_requested`
- 目前 batch 完成後才停

### 6.3 進度面板

前端輪詢：
- `/refresh-status`

route：
- `web_ui.py / refresh_status()`

前端更新位置：
- `templates/index.html` 內的 `fetch("/refresh-status")` script

顯示內容：
- running 狀態
- phase
- world
- batch 進度
- 更新筆數
- profit 重算筆數
- 錯誤訊息

## 7. Log 與下載

### 7.1 App Log

檔案：
- `config.APP_LOG_PATH`

寫入 helper：
- `web_ui.py / append_app_log()`

下載 route：
- `web_ui.py / download_app_log()`

### 7.2 Refresh Stats

檔案：
- `config.REFRESH_STATS_PATH`

寫入 helper：
- `web_ui.py / append_refresh_stats()`

下載 route：
- `web_ui.py / download_refresh_stats()`

## 8. 最近成交筆數設計

目前 `prices.daily_sales` 的語意不是 velocity，而是：
- 近三天成交筆數

計算來源：
- Universalis `/api/v2/history/{worldDcRegion}/{itemIds}`
- 取 `entries`
- 用 timestamp 過濾最近 3 天

實作位置：
- `update_prices.py / fetch_history()`
- `update_prices.py / count_recent_sales()`
- `update_prices.py / build_price_row()`

這個欄位名稱歷史上曾經存過 velocity，因此之後如果再改口徑，要先注意：
- `prices.daily_sales`
- `profits.daily_sales`
- UI 文案
- 排行過濾條件

## 9. 關鍵函式與落點速查

### 資料庫
- schema 初始化：`db.py / init_db()`
- 取得連線：`db.py / get_conn()`

### 市場更新
- 取得所有 item ids：`update_prices.py / get_item_ids()`
- 抓價格：`update_prices.py / fetch_prices()`
- 抓成交歷史：`update_prices.py / fetch_history()`
- 正規化一列價格：`update_prices.py / build_price_row()`
- 更新單一 world：`update_prices.py / update_prices_async()`
- 更新多 world：`update_prices.py / update_prices_for_worlds()`

### 利潤
- 重算全部利潤：`update_profits.py / rebuild_profits()`

### Web UI
- 主資料組裝：`web_ui.py / load_dashboard_data()`
- 查詢頁明細：`web_ui.py / load_recipe_detail()`
- 排行頁資料：`web_ui.py / get_top_profit_rows()`
- 收藏品資料：`web_ui.py / get_collectable_rows()`

### Route
- 首頁：`web_ui.py / index()`
- 單配方更新：`web_ui.py / refresh_recipe_prices()`
- 全量更新：`web_ui.py / refresh_all_prices()`
- 中斷更新：`web_ui.py / cancel_refresh()`
- 重算價差：`web_ui.py / refresh_profits()`
- 更新狀態：`web_ui.py / refresh_status()`
- 下載 app log：`web_ui.py / download_app_log()`
- 下載 refresh stats：`web_ui.py / download_refresh_stats()`

## 10. 後續開發注意事項

1. 如果改了價格口徑，不要只改模板。
   - 先確認 `prices`
   - 再確認 `profits`
   - 最後才是 UI 文案

2. 如果改了排行欄位，不要在頁面臨時計算大表。
   - 優先把邏輯收進 `update_profits.py`

3. 如果改了收藏品票數規則，先改：
   - `data/collectable_scrip_rates.csv`
   - `scripts/build_collectable_rewards.py`
   - 再重建 `data/collectable_rewards.csv`
   - 最後重新 `python import_collectable_rewards.py`

4. 如果查詢頁、排行頁、收藏品頁對同一個欄位語意不一致，先回頭檢查：
   - `load_dashboard_data()`
   - `get_top_profit_rows()`
   - `get_collectable_rows()`

## 11. 常用命令

### 啟動網站

```bash
cd /mnt/d/FF\ tools/bestmarketcrafter
source .venv-wsl/bin/activate
FF14_APP_HOST=0.0.0.0 python web_ui.py
```

### 全量刷新

```bash
cd /mnt/d/FF\ tools/bestmarketcrafter
source .venv-wsl/bin/activate
python update_prices.py
python update_profits.py
```

### 匯入收藏品票數表

```bash
cd /mnt/d/FF\ tools/bestmarketcrafter
source .venv-wsl/bin/activate
python import_collectable_rewards.py
```

### 重建收藏品對照 CSV

```bash
cd /mnt/d/FF\ tools/bestmarketcrafter
source .venv-wsl/bin/activate
python scripts/build_collectable_rewards.py
```

### 跑測試

```bash
cd /mnt/d/FF\ tools/bestmarketcrafter
source .venv-wsl/bin/activate
python -m unittest tests.test_web_ui
```
