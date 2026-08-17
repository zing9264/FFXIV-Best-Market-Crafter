"""Release 版入口:雙擊即啟動伺服器並自動開啟瀏覽器。

打包後的行為:
- 首次啟動時,把內建的種子資料(db.sqlite 與 data/)複製到執行檔旁,
  之後的更新都寫在使用者本地,不會被重新打包覆蓋。
- 自動挑選可用的連接埠(5000 在 Windows 常被系統服務占用,從 5001 開始試)。
- 伺服器就緒後自動用預設瀏覽器開啟頁面。
"""
from __future__ import annotations

import os
import shutil
import socket
import sys
import threading
import webbrowser
from pathlib import Path


def app_dir() -> Path:
    if getattr(sys, "frozen", False):
        return Path(sys.executable).parent
    return Path(__file__).parent


def bundle_dir() -> Path:
    # PyInstaller onefile 解壓目錄;非打包環境就是專案目錄
    return Path(getattr(sys, "_MEIPASS", str(Path(__file__).parent)))


def bootstrap_seed_files(base: Path) -> None:
    """首次啟動時把種子資料放到執行檔旁。已存在的一律不覆蓋。"""
    seed = bundle_dir() / "seed"
    if not seed.exists():
        return
    seed_db = seed / "db.sqlite"
    if seed_db.exists() and not (base / "db.sqlite").exists():
        shutil.copy2(seed_db, base / "db.sqlite")
    seed_data = seed / "data"
    if seed_data.exists() and not (base / "data").exists():
        shutil.copytree(seed_data, base / "data")


def pick_port(preferred: int = 5001) -> int:
    for port in [preferred, *range(5002, 5011), 5000]:
        with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as s:
            if s.connect_ex(("127.0.0.1", port)) != 0:
                return port
    return preferred


def open_browser_when_ready(url: str, port: int) -> None:
    import time

    for _ in range(60):
        with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as s:
            if s.connect_ex(("127.0.0.1", port)) == 0:
                webbrowser.open(url)
                return
        time.sleep(0.5)


def main() -> None:
    if sys.platform == "win32":
        # 讓中文 banner 在預設 Big5 主控台正常顯示
        import ctypes

        ctypes.windll.kernel32.SetConsoleOutputCP(65001)
        ctypes.windll.kernel32.SetConsoleCP(65001)

    base = app_dir()
    os.chdir(base)
    bootstrap_seed_files(base)

    port = int(os.environ.get("FF14_APP_PORT") or pick_port())
    host = os.environ.get("FF14_APP_HOST", "127.0.0.1")
    os.environ["FF14_APP_PORT"] = str(port)
    os.environ.setdefault("FF14_APP_HOST", host)

    print("=" * 52)
    print("  BestMarketCrafter - FFXIV 市場分析與製作利潤儀表板")
    print(f"  http://127.0.0.1:{port}")
    print("  關閉此視窗即停止服務")
    print("=" * 52)

    threading.Thread(
        target=open_browser_when_ready,
        args=(f"http://127.0.0.1:{port}/", port),
        daemon=True,
    ).start()

    import web_ui

    web_ui.app.run(host=host, port=port, debug=False)


if __name__ == "__main__":
    main()
