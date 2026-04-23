@echo off
REM ============================================================
REM  BestMarketCrafter 一鍵啟動
REM  - 透過 WSL Ubuntu 啟動 web_ui.py
REM  - 綁到 0.0.0.0:5000 讓 Hamachi / 區網可以連
REM  - 自動用預設瀏覽器打開 http://127.0.0.1:5000
REM ============================================================

echo [BestMarketCrafter] Starting web UI on 0.0.0.0:5000 via WSL...

REM 在新視窗跑 server,這樣本視窗關掉不影響服務,也能看到 log
start "BestMarketCrafter Web UI" wsl -e bash -c "cd '/mnt/d/FF tools/bestmarketcrafter' && source .venv-wsl/bin/activate && FF14_APP_HOST=0.0.0.0 FF14_APP_PORT=5000 python web_ui.py"

REM 等 server 起來再開瀏覽器
timeout /t 3 /nobreak >nul

echo [BestMarketCrafter] Opening browser...
start "" "http://127.0.0.1:5000"

echo.
echo 服務已在背景視窗啟動。
echo   本機:        http://127.0.0.1:5000
echo   Hamachi/區網: http://(你的IP):5000
echo.
echo 關閉該視窗或 Ctrl+C 即可停止服務。
