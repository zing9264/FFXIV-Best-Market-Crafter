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

# 打包 Release(單一 exe,含種子 db 與 data;產出在 dist/)
python -m PyInstaller BestMarketCrafter.spec
```

## Architecture Notes

- **雙定價範圍**: 同時追蹤全伺服器最低價（繁中服）與單一伺服器價格（鳳凰）
- **背景任務**: 使用 threading 處理長時間的價格更新，支援取消與進度追蹤
- **價格來源**: Universalis `/aggregated` 端點(單次呼叫同時回 world/dc/region 三層;舊的區域聚合端點長期 504 已棄用)。批次上限 100 顆(超過被靜默截斷),MAX_RPS=4, MAX_CONCURRENCY=6;全量更新約 45 秒
- **不可交易道具**: 更新前先用 `/marketable` 清單過濾,否則 aggregated 對含不可交易 id 的整批回 400
- **環境變數**: FF14_APP_HOST, FF14_APP_PORT, FF14_APP_DEBUG, FF14_DB_PATH

## Development Guidelines

- 保持繁體中文介面
- 修改 web_ui.py 路由時注意 templates/index.html 的對應 JS
- 價格相關邏輯涉及兩個 scope（region / single server），修改時兩邊都要處理
- DEV_NOTES.md 有完整的架構說明與函式對照表

## Roadmap

- **v1.2(已定案 2026-08-18)**: (1) 托盤常駐時每日自動全量更新(啟動時資料逾一日也補跑),使用者免手動;(2) 顯示伺服器可選(settings 表 + UI 下拉,陸行鳥 DC worlds,取代寫死的鳳凰)。順路可做:rebuild_profits 的相關子查詢優化(目前約 35 秒)
- **遠期**: Universalis WebSocket 訂閱 + 追加式價格觀測表(歷史走勢圖)—「每日一次」需求下暫不做,等要做走勢功能時一起上
- **程式碼簽章**: 已決定不弄(2026-08-18)—非營利、使用者僅親友,SmartScreen 靠「仍要執行」即可。若未來公開發布再評估 Azure Trusted Signing(個人約 US$10/月)
