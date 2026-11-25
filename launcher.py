from __future__ import annotations

import os
import sys
import threading
import time
import urllib.error
import urllib.request
import webbrowser

from app import main as run_server, set_update_requested_callback
from updater import schedule_update_if_available


HOST = "127.0.0.1"
PORT = int(os.environ.get("PORT", "8000"))
URL = f"http://{HOST}:{PORT}"
HEALTH_URL = f"{URL}/api/health"
UPDATE_CHECK_SECONDS = max(60, int(os.environ.get("DEEPCOIN_UPDATE_CHECK_SECONDS", "900")))


def is_ready() -> bool:
    try:
        with urllib.request.urlopen(HEALTH_URL, timeout=1.5) as response:
            if response.status != 200:
                return False
            payload = response.read().decode("utf-8")
            return '"ok": true' in payload and '"exchange": "Deepcoin"' in payload
    except (OSError, urllib.error.URLError):
        return False


def monitor_updates(update_scheduled: threading.Event) -> None:
    """Periodically stage a release and ask the launcher to exit for replacement."""
    while True:
        time.sleep(UPDATE_CHECK_SECONDS)
        try:
            if schedule_update_if_available():
                update_scheduled.set()
                return
        except Exception as error:
            print(f"自动更新检查失败，将在下次检查时重试：{error}")


def main() -> int:
    # A second click should only focus the already-running local service.
    if is_ready():
        webbrowser.open(URL)
        return 0
    if schedule_update_if_available():
        print("发现 Deepcoin 筛币器新版本，正在更新并重启。")
        return 0
    started_server = False
    update_scheduled = threading.Event()
    if getattr(sys, "frozen", False) and not os.environ.get("DEEPCOIN_DISABLE_UPDATE"):
        set_update_requested_callback(update_scheduled.set)
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
    if getattr(sys, "frozen", False) and not os.environ.get("DEEPCOIN_DISABLE_UPDATE"):
        threading.Thread(
            target=monitor_updates,
            args=(update_scheduled,),
            name="deepcoin-update-monitor",
            daemon=True,
        ).start()
    # Keep the packaged process alive so its embedded HTTP server remains available.
    try:
        while not update_scheduled.wait(1):
            pass
    except KeyboardInterrupt:
        return 0
    print("已下载并校验新版本，正在关闭旧版本以完成替换。")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
