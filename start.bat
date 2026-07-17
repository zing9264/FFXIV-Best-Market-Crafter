@echo off
chcp 65001 >nul
REM ============================================================
REM  BestMarketCrafter 一鍵啟動 (Windows 原生 Python)
REM  - 用 %~dp0 自動定位專案目錄,搬家也不用改路徑
REM  - 綁到 0.0.0.0:5001 讓 Hamachi / 區網可以連
REM    (5000 常被 Windows 系統服務占用,故改用 5001)
REM  - 自動用預設瀏覽器打開 http://127.0.0.1:5001
REM ============================================================

cd /d "%~dp0"

set "FF14_APP_HOST=0.0.0.0"
set "FF14_APP_PORT=5001"

where python >nul 2>&1
if errorlevel 1 (
    echo [BestMarketCrafter] 找不到 python,請確認已安裝並加入 PATH。
    pause
    exit /b 1
)

echo [BestMarketCrafter] Starting web UI on 0.0.0.0:5001 ...

REM 在新視窗跑 server,這樣本視窗關掉不影響服務,也能看到 log
REM (工作目錄與上面 set 的環境變數會被這個子視窗繼承,不需再設一次)
start "BestMarketCrafter Web UI" cmd /k python web_ui.py

REM 等 server 起來再開瀏覽器
timeout /t 3 /nobreak >nul

echo [BestMarketCrafter] Opening browser...
start "" "http://127.0.0.1:5001"

echo.
echo 服務已在背景視窗啟動。
echo   本機:        http://127.0.0.1:5001
echo   Hamachi/區網: http://(你的IP):5001
echo.
echo 關閉該視窗或在該視窗按 Ctrl+C 即可停止服務。
