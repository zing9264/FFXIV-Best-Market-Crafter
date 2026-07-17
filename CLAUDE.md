# FFXIV Best Market Crafter

FFXIV 市場分析與製作利潤儀表板，幫助玩家找出最賺錢的製作配方。

## Tech Stack

- **Backend**: Python 3 + Flask
- **Database**: SQLite3
- **Frontend**: 原生 HTML/CSS/JS（單頁應用）
- **外部 API**: Universalis（市場價格）、XivExdUnpacker（遊戲資料匯入）
- **語言**: 繁體中文介面

## Project Structure

```
web_ui.py              # Flask 主程式，所有路由
config.py              # 集中設定（環境變數、API 參數）
db.py                  # SQLite schema 與連線管理
update_prices.py       # 從 Universalis 抓取價格（aiohttp 非同步）
update_profits.py      # 計算利潤排行
import_tc_exd.py       # 匯入 XivExdUnpacker 的 CSV 資料
import_collectable_rewards.py  # 匯入收藏品獎勵
item_id_lookup.py      # 物品查詢工具
templates/index.html   # 前端單頁 UI
tests/test_web_ui.py   # 單元測試
scripts/               # 輔助腳本
data/                  # 收藏品 CSV 資料
```

## Key Commands

```bash
# 啟動主程式
source .venv-wsl/bin/activate
python web_ui.py

# 資料匯入
python import_tc_exd.py

# 更新價格與利潤
python update_prices.py
python update_profits.py

# 跑測試
python -m unittest tests.test_web_ui
```

## Architecture Notes

- **雙定價範圍**: 同時追蹤全伺服器最低價（繁中服）與單一伺服器價格（鳳凰）
- **背景任務**: 使用 threading 處理長時間的價格更新，支援取消與進度追蹤
- **速率限制**: Universalis API 限制 MAX_RPS=2.0, MAX_BATCH_SIZE=40, MAX_CONCURRENCY=4
- **環境變數**: FF14_APP_HOST, FF14_APP_PORT, FF14_APP_DEBUG, FF14_DB_PATH

## Development Guidelines

- 保持繁體中文介面
- 修改 web_ui.py 路由時注意 templates/index.html 的對應 JS
- 價格相關邏輯涉及兩個 scope（region / single server），修改時兩邊都要處理
- DEV_NOTES.md 有完整的架構說明與函式對照表
