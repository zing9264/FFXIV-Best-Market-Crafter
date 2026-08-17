"""Release 版入口:以系統匣(右下角小圖示)模式常駐,無主控台視窗。

行為:
- 首次啟動時,把內建的種子資料(db.sqlite 與 data/)複製到執行檔旁,
  之後的更新都寫在使用者本地,不會被重新打包覆蓋。
- 自動挑選可用的連接埠(5000 在 Windows 常被系統服務占用,從 5001 開始試)。
- 若偵測到本程式已在執行(既有連接埠回應 /health),直接開瀏覽器後結束,
  不會重複起第二份服務。
- 伺服器就緒後自動開瀏覽器;右下角小圖示右鍵選單可再次開啟頁面或結束服務。
- 所有輸出寫入執行檔旁的 server.log;未預期錯誤寫入 crash.log。
"""
from __future__ import annotations

import json
import os
import shutil
import socket
import sys
import threading
import traceback
import urllib.request
import webbrowser
from pathlib import Path

APP_NAME = "BestMarketCrafter"


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


def health_says_ok(port: int) -> bool:
    try:
        with urllib.request.urlopen(f"http://127.0.0.1:{port}/health", timeout=1) as resp:
            return json.loads(resp.read()).get("ok") is True
    except Exception:
        return False


def find_running_instance() -> int | None:
    for port in range(5000, 5011):
        if health_says_ok(port):
            return port
    return None


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


def make_tray_image():
    from PIL import Image, ImageDraw

    img = Image.new("RGBA", (64, 64), (0, 0, 0, 0))
    d = ImageDraw.Draw(img)
    # 金幣底 + 上升折線,呼應「市場利潤」主題
    d.ellipse([4, 4, 60, 60], fill=(224, 176, 48, 255), outline=(120, 84, 12, 255), width=4)
    d.line([16, 42, 28, 32, 36, 38, 48, 22], fill=(120, 84, 12, 255), width=6, joint="curve")
    d.polygon([(48, 22), (40, 24), (47, 31)], fill=(120, 84, 12, 255))
    return img


def run_tray(url: str) -> None:
    import pystray

    def on_open(icon, item):
        webbrowser.open(url)

    def on_quit(icon, item):
        icon.stop()
        os._exit(0)

    icon = pystray.Icon(
        APP_NAME,
        make_tray_image(),
        f"{APP_NAME} - {url}",
        menu=pystray.Menu(
            pystray.MenuItem("開啟儀表板", on_open, default=True),
            pystray.MenuItem("結束", on_quit),
        ),
    )
    icon.run()


def main() -> None:
    base = app_dir()
    os.chdir(base)

    # 無視窗模式下 stdout/stderr 落地成 log,方便排查
    log_file = open(base / "server.log", "a", encoding="utf-8", buffering=1)
    sys.stdout = log_file
    sys.stderr = log_file

    # 已有一份在跑:開頁面就好,不重複起服務
    existing = find_running_instance()
    if existing is not None:
        webbrowser.open(f"http://127.0.0.1:{existing}/")
        return

    bootstrap_seed_files(base)

    port = int(os.environ.get("FF14_APP_PORT") or pick_port())
    host = os.environ.get("FF14_APP_HOST", "127.0.0.1")
    os.environ["FF14_APP_PORT"] = str(port)
    os.environ.setdefault("FF14_APP_HOST", host)
    url = f"http://127.0.0.1:{port}/"

    threading.Thread(target=open_browser_when_ready, args=(url, port), daemon=True).start()

    import web_ui

    threading.Thread(
        target=lambda: web_ui.app.run(host=host, port=port, debug=False, use_reloader=False),
        daemon=True,
    ).start()

    run_tray(url)


if __name__ == "__main__":
    try:
        main()
    except Exception:
        with open(app_dir() / "crash.log", "a", encoding="utf-8") as f:
            f.write(traceback.format_exc() + "\n")
        raise
