from __future__ import annotations

import os
import sys
import threading
import time
import urllib.error
import urllib.request
import webbrowser

from app import main as run_server
from updater import schedule_update_if_available


HOST = "127.0.0.1"
PORT = int(os.environ.get("PORT", "8000"))
URL = f"http://{HOST}:{PORT}"
HEALTH_URL = f"{URL}/api/health"


def is_ready() -> bool:
    try:
        with urllib.request.urlopen(HEALTH_URL, timeout=1.5) as response:
            if response.status != 200:
                return False
            payload = response.read().decode("utf-8")
            return '"ok": true' in payload and '"exchange": "Deepcoin"' in payload
    except (OSError, urllib.error.URLError):
        return False


def main() -> int:
    if schedule_update_if_available():
        print("发现 Deepcoin 筛币器新版本，正在更新并重启。")
        return 0
    started_server = False
    if not is_ready():
        server_thread = threading.Thread(target=run_server, name="deepcoin-server", daemon=True)
        server_thread.start()
        started_server = True
        deadline = time.time() + 20
        while time.time() < deadline and not is_ready():
            time.sleep(0.25)
        if not is_ready():
            print("Deepcoin 筛选器启动失败，请检查网络或端口 8000 是否被占用。")
            return 1
    webbrowser.open(URL)
    print(f"Deepcoin 筛选器已打开：{URL}")
    if not started_server:
        return 0
    # Keep the packaged process alive so its embedded HTTP server remains available.
    try:
        while True:
            time.sleep(3600)
    except KeyboardInterrupt:
        return 0


if __name__ == "__main__":
    raise SystemExit(main())
