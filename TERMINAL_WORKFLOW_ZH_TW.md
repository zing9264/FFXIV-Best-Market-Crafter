# WezTerm + WSL + Zellij 使用說明

本文件記錄這台 Windows 開發機上的終端工作流。

目前已完成安裝：

- WezTerm
- WSL Ubuntu 24.04
- Zellij

適用情境：

- 不想再依賴 VSCode 內建終端
- 想把開發 session 留著，下次直接接回來
- 主要在本地開發，不特別處理遠端主機流程

## 1. 這套組合在做什麼

三個元件各自負責不同事情：

- WezTerm：Windows 上的終端視窗程式
- WSL Ubuntu：Linux 開發環境
- Zellij：session / pane / workspace 管理器

實際工作流程是：

1. 開 WezTerm
2. 進入 Ubuntu
3. 啟動 Zellij
4. 在 Zellij 裡開專案、跑指令、切 pane
5. 離開時把 session detach
6. 下次重新 attach 回來

重點是：

- 關掉 VSCode 沒關係
- 關掉 WezTerm 視窗也沒關係
- 只要 WSL 沒被你刻意清掉，Zellij session 可以接回來

## 2. 安裝完成後的目前狀態

Windows：

- WezTerm 已安裝

WSL：

- 發行版：`Ubuntu-24.04`
- 預設 Linux 使用者：`zing9`

Ubuntu：

- `zellij` 已安裝在 `/usr/local/bin/zellij`

## 3. 最基本的啟動方式

### 方法 A：從開始功能表開 WezTerm

開啟 `WezTerm` 後，輸入：

```powershell
wsl -d Ubuntu-24.04
```

進入 Ubuntu 後再輸入：

```bash
zellij
```

### 方法 B：直接從 PowerShell 啟動

```powershell
"C:\Program Files\WezTerm\wezterm.exe"
```

進入 WezTerm 後同樣執行：

```powershell
wsl -d Ubuntu-24.04
```

再執行：

```bash
zellij
```

## 4. 建議的日常工作流

每天開工時：

```powershell
wsl -d Ubuntu-24.04
```

```bash
zellij attach -c
```

說明：

- `attach -c` 的意思是「如果有現成 session 就接回去，沒有就新建一個」
- 這是最適合日常使用的指令

建議你之後幾乎都用這一條：

```bash
zellij attach -c
```

下班或暫停時，不要直接在 Zellij 裡把所有東西關掉，改用 detach：

- 先按 `Ctrl o`
- 再按 `d`

這樣 session 會留著，下次可直接接回來。

## 5. Zellij 最常用操作

Zellij 預設是「先按功能前綴，再按功能鍵」。

這套預設裡，你最常用的是：

- `Ctrl o`：進入操作模式
- `Ctrl o` 然後 `d`：detach session
- `Ctrl o` 然後 `n`：開新 pane
- `Ctrl o` 然後 `x`：關閉目前 pane
- `Ctrl o` 然後方向鍵：切換 pane
- `Ctrl o` 然後 `w`：顯示 pane 管理相關操作
- `Ctrl o` 然後 `s`：顯示 session 相關操作

常用 CLI 指令：

```bash
zellij
```

- 開新 session

```bash
zellij attach -c
```

- 接回現有 session，沒有就建立

```bash
zellij list-sessions
```

- 列出所有 session

```bash
zellij attach <session_name>
```

- 接回指定 session

```bash
zellij kill-session <session_name>
```

- 刪除指定 session

## 6. 建議你先學會的最小操作

只要先會這四件事就夠用了：

1. 進 Ubuntu
2. 用 `zellij attach -c`
3. 用 `Ctrl o` 再按 `n` 開新 pane
4. 用 `Ctrl o` 再按 `d` detach

如果只會這四件事，你就已經能把它當主力開發終端。

## 7. 這台機器上的專案怎麼開

你現在的專案在：

```text
D:\FF tools\bestmarketcrafter
```

在 WSL 裡對應路徑通常是：

```bash
/mnt/d/FF\ tools/bestmarketcrafter
```

進專案：

```bash
cd /mnt/d/FF\ tools/bestmarketcrafter
```

如果你要跑 Python：

```bash
python3 --version
```

如果你是直接沿用 Windows 那套 `.venv`，通常不建議在 WSL 直接混用。比較乾淨的做法是：

```bash
cd /mnt/d/FF\ tools/bestmarketcrafter
python3 -m venv .venv-wsl
source .venv-wsl/bin/activate
pip install -r requirements.txt
```

原因：

- Windows venv 和 Linux venv 不應混用
- 在 WSL 裡最好建立自己的 Linux 虛擬環境

## 8. 建議的 pane 分工

很適合你的本地開發做法：

- pane 1：專案 shell
- pane 2：跑 Flask / web UI
- pane 3：跑資料更新腳本
- pane 4：看 log / 臨時測試

例如：

pane 1：

```bash
cd /mnt/d/FF\ tools/bestmarketcrafter
source .venv-wsl/bin/activate
```

pane 2：

```bash
cd /mnt/d/FF\ tools/bestmarketcrafter
source .venv-wsl/bin/activate
python web_ui.py
```

pane 3：

```bash
cd /mnt/d/FF\ tools/bestmarketcrafter
source .venv-wsl/bin/activate
python update_prices.py
```

如果你要更新這個專案的食譜/道具資料，現在正式流程是：

Windows 端先重新解包：

```powershell
cd C:\Users\zing9\Downloads\XivExdUnpacker-win-x64
.\XivExdUnpacker.exe --language tc --sheets Item Recipe --clear
```

然後回 WSL 匯入：

```bash
cd /mnt/d/FF\ tools/bestmarketcrafter
source .venv-wsl/bin/activate
python import_tc_exd.py
```

也就是說，現在 recipe / item 更新不走舊的 API 抓法，而是：
- Windows 解包 `Item.csv` / `Recipe.csv`
- WSL 匯入 `db.sqlite`

## 9. 關掉視窗後怎麼接回來

情境：

- 你昨天開了一堆 pane
- 今天重新開 WezTerm
- 想回到昨天的狀態

步驟：

```powershell
wsl -d Ubuntu-24.04
```

```bash
zellij attach -c
```

如果你有多個 session：

```bash
zellij list-sessions
```

接著：

```bash
zellij attach <session_name>
```

## 10. 建議不要做的事

- 不要把所有工作都塞在同一個 shell 視窗
- 不要在 Zellij 裡直接 `exit` 到把整個 session 結束掉，除非你真的要關掉它
- 不要在 WSL 裡直接拿 Windows 的 `.venv` 當 Linux venv 用
- 不要把 session 保留和終端視窗保留混為一談

重點是：

- WezTerm 只是視窗
- Zellij 才是 session

## 11. 常見問題排查

### Q1. 關掉 WezTerm 後 session 不見了

先確認你是不是用 `detach` 離開：

- `Ctrl o`
- `d`

如果你直接把整個 shell 都 `exit` 掉，session 可能一起結束。

### Q2. 找不到 session

先列出：

```bash
zellij list-sessions
```

如果列表是空的，代表目前沒有存活中的 session。

### Q3. 在 WSL 裡中文路徑不好打

這是正常現象。建議：

- 用 Tab 補全
- 或先 `cd /mnt/d`
- 再逐層進去

例如：

```bash
cd /mnt/d
cd "FF tools"
cd bestmarketcrafter
```

### Q4. `sudo` 不能用

代表密碼還沒設或輸入錯了。這台機器已經有 Linux 使用者 `zing9`，之後直接用你自己設的 Linux 密碼即可。

## 12. 最實用的指令清單

Windows 端：

```powershell
wsl -d Ubuntu-24.04
```

```powershell
wsl --shutdown
```

Linux 端：

```bash
zellij attach -c
```

```bash
zellij list-sessions
```

```bash
zellij attach <session_name>
```

```bash
cd /mnt/d/FF\ tools/bestmarketcrafter
```

## 13. 最推薦的固定流程

每天固定就這樣做：

```powershell
wsl -d Ubuntu-24.04
```

```bash
cd /mnt/d/FF\ tools/bestmarketcrafter
zellij attach -c
```

要離開時：

- `Ctrl o`
- `d`

這樣就夠了。

## 14. 下一步可選優化

如果之後要再優化，最值得做的是這兩件事：

1. 把 WezTerm 設成開啟後直接進 `Ubuntu`
2. 把 shell 設成進 Ubuntu 後自動執行 `zellij attach -c`

這樣之後你只要開 WezTerm 就能直接回到工作環境。
