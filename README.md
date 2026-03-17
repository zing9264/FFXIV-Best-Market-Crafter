# FFXIV Best Market Crafter

本專案是本地用的 FF14 製作利潤看板，核心功能是：

- 匯入繁中客戶端 `Item.csv` / `Recipe.csv`
- 抓 Universalis 價格
- 用網頁查成品、素材、成本與價差

## 前置需求

在開始前，至少要有：

- Windows 可執行的 `XivExdUnpacker`
- WSL Ubuntu
- 專案自己的 Python 虛擬環境 `.venv-wsl`

### XivExdUnpacker 安裝方式

官方來源：

- GitHub: `Souma-Sumire/XivExdUnpacker`
- Repo: https://github.com/Souma-Sumire/XivExdUnpacker
- Releases: https://github.com/Souma-Sumire/XivExdUnpacker/releases

本專案不包含 `XivExdUnpacker` binary，請自行從官方 release 頁下載並解壓。

目前這個專案假設你已經把 `XivExdUnpacker` 解壓到 Windows 目錄：

```text
C:\Users\zing9\Downloads\XivExdUnpacker-win-x64
```

預設會從這裡找解包後的繁中資料：

```text
C:\Users\zing9\Downloads\XivExdUnpacker-win-x64\rawexd\tc\Item.csv
C:\Users\zing9\Downloads\XivExdUnpacker-win-x64\rawexd\tc\Recipe.csv
```

如果你放在別的位置，請修改 [`config.py`](/mnt/d/FF%20tools/bestmarketcrafter/config.py)：

- `UNPACKER_DIR`
- `RAWEXD_TC_DIR`
- `ITEM_CSV_PATH`
- `RECIPE_CSV_PATH`

最少通常只要改：

- `UNPACKER_DIR`

後面幾個預設路徑就會跟著變。

## 致謝

本專案得以實現，感謝以下服務與工具：

- Universalis
  - 提供 Final Fantasy XIV 市場看板資料 API
  - Website: https://universalis.app/
  - Docs: https://docs.universalis.app/

- XivExdUnpacker
  - 用於匯出繁中客戶端的 `Item.csv` 與 `Recipe.csv`
  - Repo: https://github.com/Souma-Sumire/XivExdUnpacker
  - Releases: https://github.com/Souma-Sumire/XivExdUnpacker/releases

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
python web_ui.py
```

預設會讀 [`config.py`](/mnt/d/FF%20tools/bestmarketcrafter/config.py) 的：

- `APP_HOST`
- `APP_PORT`
- `APP_DEBUG`

## Hamachi 私用分享

如果只打算給自己和朋友在 Hamachi 內網使用，可以把服務改成對區網開放：

```bash
cd /mnt/d/FF\ tools/bestmarketcrafter
source .venv-wsl/bin/activate
FF14_APP_HOST=0.0.0.0 FF14_APP_PORT=5000 python web_ui.py
```

朋友之後可用你的 Hamachi IP 存取：

```text
http://<你的Hamachi IP>:5000
```

注意：

- 這適合小範圍私用，不是正式公開部署
- 如果連不到，要先檢查 Windows 防火牆是否允許該 port
- 如果你跑在 WSL 內，還要確認 WSL 對 Hamachi 流量可達

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
