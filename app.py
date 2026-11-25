from __future__ import annotations

import json
import hashlib
import hmac
import math
import os
import re
import secrets
import sqlite3
import socket
import ssl
import subprocess
import sys
import tempfile
import threading
import time
from copy import deepcopy
from collections import OrderedDict
from concurrent.futures import ThreadPoolExecutor, as_completed
from http.client import RemoteDisconnected
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from typing import Any, Callable
from urllib.parse import parse_qs, quote as urlquote, urlencode, urlparse
from urllib.error import HTTPError, URLError
from urllib.request import Request, build_opener

from market_directory import MarketDirectory, MarketDirectoryError
from key_levels import DEFAULT_SETTINGS as KEY_LEVEL_SETTINGS
from key_levels import KeyLevelCalculator, REFERENCE_FEATURES, binary_features, calculate_key_levels, calculate_reference_levels, confirmed_htf_events, encode_conditions, feature_names


# PyInstaller extracts bundled static files to _MEIPASS at runtime.
ROOT = Path(getattr(sys, "_MEIPASS", Path(__file__).resolve().parent))
STATIC_DIR = ROOT / "static"


def runtime_version() -> str:
    """Read the release version beside a frozen executable for update verification."""
    if getattr(sys, "frozen", False):
        try:
            value = Path(sys.executable).with_name("version.txt").read_text(encoding="ascii").strip()
            if value:
                return value
        except OSError:
            pass
    return os.environ.get("DEEPCOIN_VERSION", "dev")


APP_VERSION = runtime_version()
_update_requested_callback: Callable[[], None] | None = None


def set_update_requested_callback(callback: Callable[[], None] | None) -> None:
    """Let the packaged launcher stop the server after a manual update is staged."""
    global _update_requested_callback
    _update_requested_callback = callback


def update_check(schedule: bool = False) -> dict[str, Any]:
    """Check GitHub and optionally stage a packaged update for launcher replacement."""
    from updater import check_for_update, schedule_update_if_available

    result = check_for_update()
    release = result.pop("_release", None)
    result["scheduled"] = False
    result["scheduleError"] = None
    if schedule and result.get("updateAvailable") and result.get("packaged"):
        if schedule_update_if_available(release=release):
            result["scheduled"] = True
            if _update_requested_callback:
                _update_requested_callback()
    return result
API_BASE = "https://api.deepcoin.com"
WEB_API_BASE = "https://net-wapi.deepcoin.com"
WEB_API_SIGN_SECRET = "61dd6c49529a05569900e71f49a0cd87"
TIMEFRAMES = {"1m": 60_000, "5m": 300_000, "15m": 900_000, "30m": 1_800_000, "1h": 3_600_000, "4h": 14_400_000, "12h": 43_200_000, "1d": 86_400_000, "1w": 604_800_000}
DEEPCOIN_BARS = {"1m": "1m", "5m": "5m", "15m": "15m", "30m": "30m", "1h": "1H", "4h": "4H", "12h": "12H", "1d": "1D", "1w": "1W"}
DEFAULT_INDICATOR_PERIOD = 20
PRICE_SCALE_INDICATORS = {"ma", "ema", "bb_upper", "bb_middle", "bb_lower"}
MAX_CANDLES = 5000
CANDLE_PAGE_SIZE = 1000
MIN_INDICATOR_HISTORY = 500
INDICATOR_HISTORY_MULTIPLIER = 10
HTTP_TIMEOUT = 8
HTTP_ATTEMPTS = 2
MAX_WORKERS = 24
CAROUSEL_MAX_SYMBOLS = 100
CAROUSEL_MIN_INTERVAL = 5
CAROUSEL_MAX_INTERVAL = 3600
_candle_cache: dict[tuple[str, str], tuple[float, list[list[float]]]] = {}
_candle_cache_coverage: dict[tuple[str, str], tuple[int, bool]] = {}
_recent_candle_cache: dict[tuple[str, str, int], tuple[float, list[list[float]]]] = {}
_candle_cache_lock = threading.Lock()
_rank_result_cache: dict[tuple[Any, ...], tuple[float, dict[str, Any]]] = {}
_rank_result_locks: dict[tuple[Any, ...], threading.Lock] = {}
_rank_result_guard = threading.Lock()
_key_level_snapshot_cache: OrderedDict[tuple[Any, ...], dict[str, Any] | None] = OrderedDict()
_key_level_snapshot_cache_lock = threading.Lock()
KEY_LEVEL_SNAPSHOT_CACHE_SIZE = 2048
_candle_db_lock = threading.Lock()
_cache_root = Path(os.environ.get("LOCALAPPDATA") or (Path.home() / ".cache")) / "DeepcoinScreen"
CANDLE_CACHE_PATH = _cache_root / "candles.sqlite3"
KEY_LEVEL_FEATURES = feature_names()
KEY_LEVEL_RECENT_HAMMER_FEATURE = "kl_event_support_hammer_recent"
KEY_LEVEL_FEATURES[KEY_LEVEL_RECENT_HAMMER_FEATURE] = "近期支撑锤型拒绝"
KEY_LEVEL_RECENT_INVERTED_HAMMER_FEATURE = "kl_event_resistance_inverted_hammer_recent"
KEY_LEVEL_FEATURES[KEY_LEVEL_RECENT_INVERTED_HAMMER_FEATURE] = "近期压力位长上影拒绝"
KEY_LEVEL_BINARY_FEATURES = binary_features()
KEY_LEVEL_BINARY_FEATURES.update({
    KEY_LEVEL_RECENT_HAMMER_FEATURE, KEY_LEVEL_RECENT_INVERTED_HAMMER_FEATURE,
})
KEY_LEVEL_REFERENCE_FEATURES = set(REFERENCE_FEATURES)
KEY_LEVEL_DYNAMIC_FEATURES = set(KEY_LEVEL_FEATURES) - KEY_LEVEL_REFERENCE_FEATURES
KEY_LEVEL_HAMMER_LOOKBACK_DEFAULT = 24
KEY_LEVEL_HAMMER_LOOKBACK_MAX = 100
KEY_LEVEL_SIGNAL_THRESHOLDS = {
    "high_quality_support": 75.0,
    "high_quality_resistance": 75.0,
    "support_near": 0.5,
    "resistance_near": 0.5,
    "support_hammer_rejection": 60.0,
    "resistance_inverted_hammer_rejection": 60.0,
}
KEY_LEVEL_SIGNALS = {
    "support_exists", "resistance_exists", *KEY_LEVEL_SIGNAL_THRESHOLDS,
    "support_touch", "support_hammer_rejection", "resistance_inverted_hammer_rejection",
    "resistance_touch", "support_break", "resistance_break",
    "support_retest", "resistance_retest", "price_above_vwap", "price_below_vwap",
    "price_above_previous_day_high", "price_below_previous_day_low",
    "price_above_previous_week_high", "price_below_previous_week_low",
    "price_above_day_open", "price_below_day_open",
}
KEY_LEVEL_BASE_HISTORY = (
    int(KEY_LEVEL_SETTINGS["maxZoneAge"])
    + int(KEY_LEVEL_SETTINGS["pivotLeft"])
    + int(KEY_LEVEL_SETTINGS["pivotRight"])
    + int(KEY_LEVEL_SETTINGS["atrLength"]) * 8
)
KEY_LEVEL_HTF_WARMUP = (
    int(KEY_LEVEL_SETTINGS["atrLength"]) * 8
    + int(KEY_LEVEL_SETTINGS["htfPivotLeft"])
    + int(KEY_LEVEL_SETTINGS["htfPivotRight"])
    + 2
)


def carousel_displays() -> list[dict[str, Any]]:
    """Return physical monitor bounds used by the Windows carousel launcher."""
    if os.name != "nt":
        return [{"index": 0, "name": "当前窗口", "left": 0, "top": 0, "width": 0, "height": 0, "primary": True}]
    import ctypes
    from ctypes import wintypes

    user32 = ctypes.windll.user32
    monitors: list[dict[str, Any]] = []

    class Rect(ctypes.Structure):
        _fields_ = [("left", ctypes.c_long), ("top", ctypes.c_long),
                    ("right", ctypes.c_long), ("bottom", ctypes.c_long)]

    class MonitorInfo(ctypes.Structure):
        _fields_ = [("cbSize", wintypes.DWORD), ("rcMonitor", Rect),
                    ("rcWork", Rect), ("dwFlags", wintypes.DWORD),
                    ("szDevice", wintypes.WCHAR * 32)]

    monitor_enum_proc = ctypes.WINFUNCTYPE(
        wintypes.BOOL, ctypes.c_void_p, ctypes.c_void_p,
        ctypes.POINTER(Rect), wintypes.LPARAM,
    )

    @monitor_enum_proc
    def callback(handle, _hdc, _rect, _data):
        info = MonitorInfo()
        info.cbSize = ctypes.sizeof(MonitorInfo)
        if user32.GetMonitorInfoW(handle, ctypes.byref(info)):
            rect = info.rcWork
            device_name = str(info.szDevice).strip()
            device_match = re.search(r"DISPLAY(\d+)", device_name, re.IGNORECASE)
            screen_number = int(device_match.group(1)) if device_match else len(monitors) + 1
            monitors.append({
                "name": str(info.szDevice).strip() or f"显示器 {len(monitors) + 1}",
                "screenNumber": screen_number,
                "name": f"DISPLAY{screen_number}",
                "left": int(rect.left), "top": int(rect.top),
                "width": int(rect.right - rect.left),
                "height": int(rect.bottom - rect.top),
                "primary": bool(info.dwFlags & 1),
            })
        return True

    user32.EnumDisplayMonitors(0, 0, callback, 0)
    monitors.sort(key=lambda item: (not item["primary"], item["top"], item["left"]))
    for index, monitor in enumerate(monitors):
        monitor["index"] = index
    return monitors or [{"index": 0, "name": "主显示器", "left": 0, "top": 0, "width": 0, "height": 0, "primary": True}]


def _carousel_browser() -> str | None:
    configured = os.environ.get("DEEPCOIN_BROWSER", "").strip().strip('"')
    candidates = [configured] if configured else []
    candidates.extend([
        os.path.expandvars(r"%ProgramFiles(x86)%\Microsoft\Edge\Application\msedge.exe"),
        os.path.expandvars(r"%ProgramFiles%\Microsoft\Edge\Application\msedge.exe"),
        os.path.expandvars(r"%ProgramFiles%\Google\Chrome\Application\chrome.exe"),
    ])
    return next((candidate for candidate in candidates if candidate and Path(candidate).is_file()), None)


def _position_carousel_window(process_id: int, display: dict[str, Any]) -> None:
    if os.name != "nt" or not display.get("width") or not display.get("height"):
        return
    import ctypes
    from ctypes import wintypes
    user32 = ctypes.windll.user32
    hwnd_found = {"value": 0}
    enum_proc = ctypes.WINFUNCTYPE(wintypes.BOOL, wintypes.HWND, wintypes.LPARAM)

    @enum_proc
    def callback(hwnd, _data):
        process = wintypes.DWORD()
        user32.GetWindowThreadProcessId(hwnd, ctypes.byref(process))
        if process.value == process_id and user32.IsWindowVisible(hwnd):
            hwnd_found["value"] = hwnd
            return False
        return True

    for _ in range(80):
        user32.EnumWindows(callback, 0)
        hwnd = hwnd_found["value"]
        if hwnd:
            user32.ShowWindow(hwnd, 3)  # SW_MAXIMIZE; the app window fills the work area.
            user32.SetWindowPos(
                hwnd, 0, int(display["left"]), int(display["top"]),
                int(display["width"]), int(display["height"]), 0x0040,
            )
            return
        time.sleep(0.125)


def open_carousel_window(payload: dict[str, Any]) -> dict[str, Any]:
    """Launch the carousel as a dedicated browser app and place it on one monitor."""
    inst_ids = [str(item).strip() for item in payload.get("instIds", []) if str(item).strip()]
    if not inst_ids or len(inst_ids) > CAROUSEL_MAX_SYMBOLS:
        raise ValueError(f"轮播交易对数量应为 1 到 {CAROUSEL_MAX_SYMBOLS} 个")
    timeframe = str(payload.get("timeframe", "1h"))
    if timeframe not in TIMEFRAMES:
        raise ValueError("不支持的轮播周期")
    mode = str(payload.get("mode", "close"))
    if mode not in {"close", "live"}:
        raise ValueError("不支持的轮播模式")
    source = str(payload.get("source", "screen"))
    if source not in {"screen", "ranking"}:
        raise ValueError("不支持的轮播来源")
    try:
        interval = int(payload.get("interval", 15))
    except (TypeError, ValueError):
        interval = 15
    interval = max(CAROUSEL_MIN_INTERVAL, min(CAROUSEL_MAX_INTERVAL, interval))
    displays = carousel_displays()
    try:
        display_index = int(payload.get("displayIndex", 0))
    except (TypeError, ValueError):
        display_index = 0
    display = next((item for item in displays if item["index"] == display_index), None)
    if display is None:
        raise ValueError("指定显示器不存在，请重新打开轮播设置")
    current_ids = {market["id"] for market in fetch_markets()}
    invalid_ids = sorted(set(inst_ids) - current_ids)
    if invalid_ids:
        raise ValueError("部分交易对已不在当前官网核验目录，请重新筛选后轮播")
    conditions = payload.get("conditions") or []
    if not isinstance(conditions, list) or len(conditions) > 12:
        raise ValueError("轮播条件格式无效")
    validate_conditions(conditions)
    chart_indicators = validate_chart_indicators(payload.get("chartIndicators"))
    rank_params: dict[str, Any] = {}
    refresh_seconds = 0
    if source == "ranking":
        ranking = payload.get("ranking") or {}
        rank_timeframe = str(ranking.get("timeframe", timeframe))
        if rank_timeframe not in TIMEFRAMES:
            raise ValueError("不支持的涨跌幅排序周期")
        rank_direction = str(ranking.get("direction", "desc"))
        if rank_direction not in {"asc", "desc"}:
            raise ValueError("涨跌幅排序方向无效")
        rank_quote = str(ranking.get("quote", payload.get("quote", "USDT"))).upper()
        if rank_quote not in {"USDT", "USDC"}:
            raise ValueError("不支持的结算币")
        try:
            rank_max_symbols = int(ranking.get("maxSymbols", 100))
            refresh_seconds = int(ranking.get("refreshSeconds", 0))
            first_refresh_seconds = int(ranking.get("firstRefreshSeconds", refresh_seconds))
        except (TypeError, ValueError):
            raise ValueError("涨跌幅自动更新设置无效") from None
        rank_max_symbols = max(1, min(1000, rank_max_symbols))
        refresh_seconds = 0 if refresh_seconds <= 0 else max(30, min(3600, refresh_seconds))
        first_refresh_seconds = max(1, min(refresh_seconds, first_refresh_seconds)) if refresh_seconds else 0
        rank_params = {
            "rankQuote": rank_quote,
            "rankTimeframe": rank_timeframe,
            "rankDirection": rank_direction,
            "rankMaxSymbols": rank_max_symbols,
            "rankSymbols": str(ranking.get("symbols", "")),
            "rankRefreshSeconds": refresh_seconds,
            "rankFirstRefreshSeconds": first_refresh_seconds,
        }
    port = int(os.environ.get("PORT", "8000"))
    query = urlencode({
        "instIds": ",".join(inst_ids), "quote": str(payload.get("quote", "USDT")).upper(),
        "timeframe": timeframe, "mode": mode, "interval": interval,
        "source": source, **rank_params,
        "chartIndicators": json.dumps(chart_indicators, separators=(",", ":")),
        "conditions": json.dumps(conditions, ensure_ascii=False, separators=(",", ":")),
    })
    url = f"http://127.0.0.1:{port}/carousel?{query}"
    browser = _carousel_browser()
    if not browser:
        raise ValueError("未找到 Microsoft Edge 或 Google Chrome，无法定位到指定显示器")
    creationflags = getattr(subprocess, "CREATE_NO_WINDOW", 0)
    profile_dir = Path(tempfile.gettempdir()) / f"DeepcoinScreen-carousel-{os.getpid()}-{secrets.token_hex(4)}"
    profile_dir.mkdir(parents=True, exist_ok=False)
    process = subprocess.Popen(
        [browser, "--new-window", f"--user-data-dir={profile_dir}", "--no-first-run", f"--app={url}"],
        creationflags=creationflags,
        close_fds=True,
    )
    threading.Thread(
        target=_position_carousel_window, args=(process.pid, display),
        name="deepcoin-carousel-window", daemon=True,
    ).start()
    return {
        "ok": True, "pid": process.pid, "url": url, "display": display,
        "interval": interval, "source": source, "rankRefreshSeconds": refresh_seconds,
    }


class DeepcoinError(RuntimeError):
    pass


def _retryable_request_error(exc: Exception, request: Request | None = None) -> bool:
    if isinstance(exc, HTTPError):
        transient_candle_auth = (
            exc.code == 401 and request is not None
            and urlparse(request.full_url).path == "/deepcoin/market/candles"
        )
        return transient_candle_auth or exc.code in {408, 425, 429} or 500 <= exc.code < 600
    reason = exc.reason if isinstance(exc, URLError) else exc
    return isinstance(reason, (TimeoutError, socket.timeout, ConnectionResetError, RemoteDisconnected, ssl.SSLEOFError))


def _request_error_message(service: str, url: str, exc: Exception) -> str:
    host = urlparse(url).hostname or "Deepcoin"
    reason = exc.reason if isinstance(exc, URLError) else exc
    if isinstance(exc, HTTPError):
        return f"{service}返回 HTTP {exc.code}（{host}）"
    if isinstance(reason, (TimeoutError, socket.timeout)):
        return f"{service}连接超时（{host}）。请检查网络/VPN/代理后重试。"
    if isinstance(reason, ConnectionRefusedError):
        return f"{service}连接被拒绝（{host}）。请检查网络/VPN/代理后重试。"
    if isinstance(reason, socket.gaierror):
        return f"{service}域名解析失败（{host}）。请检查 DNS、网络或代理设置。"
    return f"{service}请求失败（{host}）：{exc!r}"


def _request_json(request: Request, service: str) -> Any:
    last_error: Exception | None = None
    opener = build_opener()
    for attempt in range(HTTP_ATTEMPTS):
        try:
            with opener.open(request, timeout=HTTP_TIMEOUT) as response:
                raw = response.read().decode("utf-8")
        except Exception as exc:
            if isinstance(exc, HTTPError):
                exc.close()
            last_error = exc
            if attempt + 1 < HTTP_ATTEMPTS and _retryable_request_error(exc, request):
                time.sleep(0.25)
                continue
            raise DeepcoinError(_request_error_message(service, request.full_url, exc)) from exc
        try:
            return json.loads(raw)
        except (TypeError, ValueError) as exc:
            raise DeepcoinError(f"{service}返回的 JSON 格式无效") from exc
    raise DeepcoinError(_request_error_message(service, request.full_url, last_error or RuntimeError("未知错误")))


def deepcoin_get(path: str, params: dict[str, Any] | None = None) -> Any:
    query = "?" + urlencode({key: value for key, value in (params or {}).items() if value is not None}) if params else ""
    request = Request(
        API_BASE + path + query,
        headers={"Accept": "application/json", "User-Agent": "deepcoin-screen/1.0"},
    )
    payload = _request_json(request, "Deepcoin 行情接口")
    if not isinstance(payload, dict):
        raise DeepcoinError("Deepcoin 行情接口返回的数据结构无效")
    if str(payload.get("code")) != "0":
        raise DeepcoinError(payload.get("msg") or "Deepcoin 返回了错误")
    return payload.get("data")


def website_catalog_request(system: str | None = None) -> dict[str, Any]:
    """Read anonymous website metadata; no account credentials are involved."""
    alphabet = "ABCDEFGHJKMNPQRSTWXYZabcdefhijkmnprstwxyz2345678"
    # The public website signs its anonymous catalogue request with sorted
    # scalar parameters. Keep this isolated so a website API change is easy to
    # diagnose without affecting market data requests.
    params = {"appid": 547798, "randomstr": "".join(secrets.choice(alphabet) for _ in range(6)), "timestamp": int(time.time() * 1000), "convertPOST": 1}
    if system is not None:
        params.update(showall=0, flag=1, system=system)
    signing_query = urlencode(sorted(params.items()))
    digest = hmac.new(WEB_API_SIGN_SECRET.encode("ascii"), signing_query.encode("utf-8"), hashlib.sha256).hexdigest()
    params["sign"] = hashlib.md5(digest.encode("ascii")).hexdigest()
    request = Request(
        WEB_API_BASE + ("/api/common/symbol/list-all?" + urlencode(params) if system is not None else "/api/common/symbol/list"),
        data=None if system is not None else json.dumps(params).encode("utf-8"),
        headers={"Accept": "application/json", "Content-Type": "application/json;charset=utf-8", "User-Agent": "Mozilla/5.0", "Origin": "https://www.deepcoin.com", "Referer": "https://www.deepcoin.com/", "platform": "pc", "x-requested-with": "XMLHttpRequest", "Cache-Control": "no-cache"},
    )
    payload = _request_json(request, "Deepcoin 官网合约清单接口")
    if not isinstance(payload, dict) or str(payload.get("retCode")) != "0" or not isinstance(payload.get("retData"), dict):
        raise DeepcoinError("官网合约清单结构或返回码异常")
    return payload["retData"]


def website_symbols(rows: Any) -> set[str]:
    if not isinstance(rows, list) or any(not isinstance(row, dict) or not row.get("InstrumentID") for row in rows):
        raise DeepcoinError("官网合约清单字段缺失或格式变化")
    return {str(row["InstrumentID"]).strip().upper() for row in rows if str(row.get("IsShow", "1")) == "1"}


def website_catalog_get(system: str) -> set[str]:
    return website_symbols(website_catalog_request(system).get("data"))


def website_route_catalog_get(systems: set[str]) -> dict[str, set[str]]:
    data = website_catalog_request()
    return {system: website_symbols(data[system].get("instrument_list")) for system in systems if isinstance(data.get(system), dict)}


market_directory = MarketDirectory(
    lambda: deepcoin_get("/deepcoin/market/instruments", {"instType": "SWAP"}),
    lambda system: website_catalog_get(system),
    lambda systems: website_route_catalog_get(systems),
)


def website_catalog_health(quote: str | None = None) -> dict[str, Any]:
    health = market_directory.health()
    if quote and quote in health.get("quotes", {}):
        counts = health["quotes"][quote]
        health.update(publicMarkets=counts["public"], availableMarkets=counts["available"], excludedMarkets=counts["excluded"], pendingMarkets=counts["pending"])
    return health


def number(value: Any) -> float | None:
    try:
        result = float(value)
        return result if math.isfinite(result) else None
    except (TypeError, ValueError):
        return None


def market_trade_navigation(market: dict[str, Any]) -> dict[str, Any]:
    """Build a route only after the current official catalogue confirms it."""
    confirmed = market.get("webCatalogStatus") == "available" and market.get("webListed") is True
    return {
        "tradeUrl": market.get("tradeUrl") if confirmed else None,
        "webSymbol": market.get("webSymbol"),
        "webTradeStatus": "available" if confirmed else "unverified",
        "webTradeReason": "" if confirmed else "官网入口尚未确认",
    }


def fetch_markets() -> list[dict[str, Any]]:
    return market_directory.get_markets()


def finalize_market_response(payload: dict[str, Any], request: dict[str, Any]) -> dict[str, Any]:
    """Recheck the universe if it changed while candles were being scanned."""
    quote = str(request.get("quote", "USDT")).upper()
    current = [market for market in fetch_markets() if market["quote"] == quote]
    ids = {market["id"] for market in current}
    for key in ("results", "closeResults", "liveResults"):
        if key in payload:
            payload[key] = [item for item in payload[key] if item.get("instId") in ids]
    if "direction" in payload:
        for rank, item in enumerate(payload.get("results", []), 1):
            item["rank"] = rank
    payload["totalMarkets"] = len(current)
    payload["websiteCatalog"] = website_catalog_health(quote)
    requested = {symbol.strip().upper().removesuffix("-SWAP").replace("-", "") for symbol in str(request.get("symbols", "")).split(",") if symbol.strip()}
    names = {value for market in current for value in (market["symbol"], market["id"].upper().removesuffix("-SWAP").replace("-", ""))}
    payload["unavailableSymbols"] = sorted(requested - names)
    return payload


def fetch_tickers() -> dict[str, dict[str, Any]]:
    data = deepcoin_get("/deepcoin/market/tickers", {"instType": "SWAP"}) or []
    result = {}
    for item in data:
        if item.get("instId"):
            last = number(item.get("last"))
            opened = number(item.get("open24h"))
            result[item["instId"]] = {
                "last": last,
                "high": number(item.get("high24h")),
                "low": number(item.get("low24h")),
                "volume24h": number(item.get("volCcy24h")),
                "change24h": ((last - opened) / opened * 100) if last is not None and opened else None,
                "bid": number(item.get("bidPx")),
                "ask": number(item.get("askPx")),
                "timestamp": int(item.get("ts")) if str(item.get("ts", "")).isdigit() else None,
            }
    return result


def expected_last_candle_open(candles: list[list[float]], duration: int, now_ms: int) -> int:
    """Infer the exchange's candle boundary from returned timestamps (daily bars are UTC+8 aligned)."""
    if not candles:
        return 0
    offset = int(candles[-1][0]) % duration
    return ((now_ms - offset) // duration) * duration + offset - duration


def parse_candle_rows(rows: Any) -> list[list[float]]:
    candles = []
    for row in rows or []:
        if len(row) < 6:
            continue
        parsed = [number(value) for value in row[:6]]
        if all(value is not None for value in parsed):
            parsed[0] = int(parsed[0])
            candles.append(parsed)  # timestamp, open, high, low, close, volume
    return candles


def request_candle_page(inst_id: str, timeframe: str, limit: int, after: int | None = None) -> list[list[float]]:
    rows = deepcoin_get(
        "/deepcoin/market/candles",
        {"instId": inst_id, "bar": DEEPCOIN_BARS[timeframe], "limit": limit, "after": after},
    ) or []
    return parse_candle_rows(rows)


def fetch_recent_candles(inst_id: str, timeframe: str, limit: int = 20) -> list[list[float]]:
    """Fetch a short current-inclusive window for Pine's daily/weekly references and VWAP."""
    limit = max(2, min(CANDLE_PAGE_SIZE, int(limit)))
    cache_key = (inst_id, timeframe, limit)
    now = time.time()
    with _candle_cache_lock:
        cached = _recent_candle_cache.get(cache_key)
        if cached and now - cached[0] < 20:
            return list(cached[1])
    candles = request_candle_page(inst_id, timeframe, limit)
    candles = sorted({int(row[0]): row for row in candles}.values(), key=lambda row: int(row[0]))[-limit:]
    with _candle_cache_lock:
        _recent_candle_cache[cache_key] = (now, candles)
    return candles


def load_persisted_candles(cache_key: tuple[str, str]) -> tuple[float, list[list[float]]] | None:
    if not CANDLE_CACHE_PATH.is_file():
        return None
    connection = None
    try:
        connection = sqlite3.connect(CANDLE_CACHE_PATH, timeout=3)
        row = connection.execute(
            "SELECT fetched_at, candles FROM candle_cache WHERE inst_id = ? AND timeframe = ?",
            cache_key,
        ).fetchone()
        if not row:
            return None
        candles = json.loads(row[1])
        if not isinstance(candles, list) or any(not isinstance(item, list) or len(item) < 6 for item in candles):
            return None
        return float(row[0]), candles
    except (OSError, sqlite3.Error, ValueError, TypeError):
        return None
    finally:
        if connection is not None:
            connection.close()


def save_persisted_candles(cache_key: tuple[str, str], fetched_at: float,
                           candles: list[list[float]]) -> None:
    try:
        CANDLE_CACHE_PATH.parent.mkdir(parents=True, exist_ok=True)
        payload = json.dumps(candles, separators=(",", ":"))
        with _candle_db_lock:
            connection = sqlite3.connect(CANDLE_CACHE_PATH, timeout=5)
            try:
                with connection:
                    connection.execute(
                        "CREATE TABLE IF NOT EXISTS candle_cache ("
                        "inst_id TEXT NOT NULL, timeframe TEXT NOT NULL, fetched_at REAL NOT NULL, "
                        "candles TEXT NOT NULL, PRIMARY KEY (inst_id, timeframe))"
                    )
                    connection.execute(
                        "INSERT INTO candle_cache (inst_id, timeframe, fetched_at, candles) VALUES (?, ?, ?, ?) "
                        "ON CONFLICT(inst_id, timeframe) DO UPDATE SET fetched_at=excluded.fetched_at, candles=excluded.candles",
                        (cache_key[0], cache_key[1], fetched_at, payload),
                    )
            finally:
                connection.close()
    except (OSError, sqlite3.Error, TypeError, ValueError):
        return


def fetch_candles(inst_id: str, timeframe: str, required_period: int = DEFAULT_INDICATOR_PERIOD, limit_override: int | None = None) -> list[list[float]]:
    cache_key = (inst_id, timeframe)
    required_limit = limit_override if limit_override is not None else max(70, required_period + 30)
    required_limit = max(2, min(MAX_CANDLES, required_limit))
    raw_target_limit = min(MAX_CANDLES, required_limit + 1)
    duration = TIMEFRAMES[timeframe]
    now_ms = int(time.time() * 1000)
    cached_bars: list[list[float]] = []
    cached_at = 0.0
    covered_limit = 0
    history_exhausted = False
    with _candle_cache_lock:
        cached = _candle_cache.get(cache_key)
        if cached:
            cached_at, cached_bars = cached[0], list(cached[1])
            covered_limit, history_exhausted = _candle_cache_coverage.get(
                cache_key, (len(cached_bars), False)
            )
    if not cached_bars:
        persisted = load_persisted_candles(cache_key)
        if persisted:
            cached_at, cached_bars = persisted[0], list(persisted[1])
            with _candle_cache_lock:
                _candle_cache.setdefault(cache_key, (cached_at, cached_bars))
                covered_limit, history_exhausted = _candle_cache_coverage.setdefault(
                    cache_key, (len(cached_bars), False)
                )
    expected_open = expected_last_candle_open(cached_bars, duration, now_ms)
    cache_is_full = len(cached_bars) >= required_limit or covered_limit >= required_limit or history_exhausted
    cache_is_current = bool(cached_bars and cached_bars[-1][0] >= expected_open)
    if cache_is_full and (cache_is_current or time.time() - cached_at < 10):
        return cached_bars[-required_limit:]

    candles: list[list[float]] = []
    can_increment = cache_is_full and bool(cached_bars)
    missing_bars = max(0, int((expected_open - cached_bars[-1][0]) // duration)) if cached_bars else 0
    if can_increment and missing_bars <= CANDLE_PAGE_SIZE - 2:
        recent = request_candle_page(inst_id, timeframe, min(CANDLE_PAGE_SIZE, max(3, missing_bars + 2)))
        by_timestamp = {int(row[0]): row for row in cached_bars}
        by_timestamp.update({int(row[0]): row for row in recent})
        candles = [by_timestamp[key] for key in sorted(by_timestamp)][-raw_target_limit:]
    else:
        history_exhausted = False
        by_timestamp: dict[int, list[float]] = {}
        after = None
        while len(by_timestamp) < raw_target_limit:
            overlap = 1 if after is not None else 0
            page_limit = min(CANDLE_PAGE_SIZE, raw_target_limit - len(by_timestamp) + overlap)
            page = request_candle_page(inst_id, timeframe, page_limit, after)
            if not page:
                history_exhausted = True
                break
            for row in page:
                by_timestamp[int(row[0])] = row
            oldest = min(int(row[0]) for row in page)
            if after is not None and oldest >= after:
                break
            if len(by_timestamp) >= raw_target_limit:
                break
            if len(page) < page_limit:
                # Some Deepcoin gateways return a short first page even when
                # older candles exist. Continue with `after` once so a chart
                # is not reduced to the first three returned candles.
                # A genuinely new listing will return an empty next page and
                # then be cached as history-exhausted.
                after = oldest
                continue
            after = oldest
        candles = [by_timestamp[key] for key in sorted(by_timestamp)][-raw_target_limit:]

    if candles and candles[-1][0] + duration > now_ms:
        candles.pop()
    candles = candles[-required_limit:]
    fetched_at = time.time()
    with _candle_cache_lock:
        _candle_cache[cache_key] = (fetched_at, candles)
        _candle_cache_coverage[cache_key] = (max(covered_limit, required_limit), history_exhausted)
    save_persisted_candles(cache_key, fetched_at, candles)
    return candles


def cached_key_level_snapshot(inst_id: str, timeframe: str, base_bars: list[list[float]],
                              htf_bars: list[list[float]], base_duration_ms: int,
                              htf_duration_ms: int, tick_size: float) -> dict[str, Any] | None:
    base_signature = hashlib.sha256(
        json.dumps(base_bars, separators=(",", ":")).encode("ascii")
    ).digest()
    htf_signature = hashlib.sha256(
        json.dumps(htf_bars, separators=(",", ":")).encode("ascii")
    ).digest()
    cache_key = (
        inst_id, timeframe, base_duration_ms, htf_duration_ms, float(tick_size or 0.0),
        len(base_bars), base_bars[0][0] if base_bars else None,
        base_bars[-1][0] if base_bars else None, base_signature,
        len(htf_bars), htf_bars[0][0] if htf_bars else None,
        htf_bars[-1][0] if htf_bars else None, htf_signature,
    )
    with _key_level_snapshot_cache_lock:
        if cache_key in _key_level_snapshot_cache:
            _key_level_snapshot_cache.move_to_end(cache_key)
            return _key_level_snapshot_cache[cache_key]

    snapshot = calculate_key_levels(
        base_bars,
        htf_bars,
        base_duration_ms=base_duration_ms,
        htf_duration_ms=htf_duration_ms,
        tick_size=tick_size,
    )
    with _key_level_snapshot_cache_lock:
        _key_level_snapshot_cache[cache_key] = snapshot
        _key_level_snapshot_cache.move_to_end(cache_key)
        while len(_key_level_snapshot_cache) > KEY_LEVEL_SNAPSHOT_CACHE_SIZE:
            _key_level_snapshot_cache.popitem(last=False)
    return snapshot


def sma(values: list[float], period: int) -> float | None:
    if period <= 0 or len(values) < period:
        return None
    return sum(values[-period:]) / period


def ema(values: list[float], period: int) -> float | None:
    if period <= 0 or len(values) < period:
        return None
    current = sum(values[:period]) / period
    multiplier = 2 / (period + 1)
    for value in values[period:]:
        current = (value - current) * multiplier + current
    return current


def bollinger(values: list[float], period: int, deviations: float) -> tuple[float | None, float | None, float | None, float | None]:
    if period <= 0 or len(values) < period:
        return None, None, None, None
    window = values[-period:]
    middle = sum(window) / period
    variance = sum((value - middle) ** 2 for value in window) / period
    width = math.sqrt(variance) * deviations
    upper, lower = middle + width, middle - width
    return upper, middle, lower, (upper - lower) / middle * 100 if middle else None


def rsi(values: list[float], period: int) -> float | None:
    if period <= 0 or len(values) <= period:
        return None
    changes = [values[index] - values[index - 1] for index in range(1, len(values))]
    gains = [max(change, 0) for change in changes]
    losses = [max(-change, 0) for change in changes]
    average_gain = sum(gains[:period]) / period
    average_loss = sum(losses[:period]) / period
    for index in range(period, len(changes)):
        average_gain = (average_gain * (period - 1) + gains[index]) / period
        average_loss = (average_loss * (period - 1) + losses[index]) / period
    if average_loss == 0:
        return 100.0
    return 100 - 100 / (1 + average_gain / average_loss)


def indicator_value(name: str, closes: list[float], period: int = DEFAULT_INDICATOR_PERIOD, deviations: float = 2) -> float | None:
    if name == "price":
        return closes[-1] if closes else None
    if name == "ma":
        return sma(closes, period)
    if name == "ema":
        return ema(closes, period)
    if name == "rsi":
        return rsi(closes, period)
    upper, middle, lower, width = bollinger(closes, period, deviations)
    return {"bb_upper": upper, "bb_middle": middle, "bb_lower": lower, "bb_width": width}.get(name)


def format_value(value: float | None) -> float | None:
    return round(value, 10) if value is not None else None


def make_metrics(market: dict[str, Any], ticker: dict[str, Any], candles: list[list[float]], live: bool = False,
                 key_level_features: dict[str, float | None] | None = None) -> dict[str, Any]:
    closes = [row[4] for row in candles]
    calculation_closes = list(closes)
    if live and ticker.get("last") is not None:
        calculation_closes.append(ticker["last"])
    display_price = ticker.get("last") if live else (closes[-1] if closes else None)
    upper, middle, lower, width = bollinger(calculation_closes, 20, 2)
    return {
        "symbol": market["symbol"],
        "instId": market["id"],
        "base": market["base"],
        "quote": market["quote"],
        "assetType": market.get("assetType"),
        **market_trade_navigation(market),
        "price": format_value(display_price),
        "closePrice": format_value(closes[-1] if closes else None),
        "livePrice": format_value(ticker.get("last")),
        "change24h": format_value(ticker.get("change24h")),
        "volume24h": format_value(ticker.get("volume24h")),
        "high24h": format_value(ticker.get("high")),
        "low24h": format_value(ticker.get("low")),
        "ema20": format_value(ema(calculation_closes, 20)),
        "ma20": format_value(sma(calculation_closes, 20)),
        "rsi14": format_value(rsi(calculation_closes, 14)),
        "bbUpper": format_value(upper),
        "bbMiddle": format_value(middle),
        "bbLower": format_value(lower),
        "bbWidth": format_value(width),
        "candles": len(candles),
        "timestamp": ticker.get("timestamp"),
        **(key_level_features or {}),
    }


def resolve_indicator(condition: dict[str, Any], metrics: dict[str, Any], closes: list[float]) -> float | None:
    indicator = str(condition.get("indicator", "price"))
    if indicator in {"change24h", "volume24h"}:
        return number(metrics.get(indicator))
    if indicator == KEY_LEVEL_RECENT_HAMMER_FEATURE:
        history = metrics.get("_supportHammerHistory") or []
        lookback = int(number(condition.get("keyLevelLookbackBars")) or KEY_LEVEL_HAMMER_LOOKBACK_DEFAULT)
        lookback = max(1, min(lookback, KEY_LEVEL_HAMMER_LOOKBACK_MAX))
        threshold = number(condition.get("keyLevelThreshold"))
        threshold = KEY_LEVEL_SIGNAL_THRESHOLDS["support_hammer_rejection"] if threshold is None else threshold
        return float(any(
            isinstance(item, dict)
            and item.get("event")
            and number(item.get("supportScore")) is not None
            and number(item.get("supportScore")) >= threshold
            for item in history[-lookback:]
        ))
    if indicator == KEY_LEVEL_RECENT_INVERTED_HAMMER_FEATURE:
        history = metrics.get("_resistanceInvertedHammerHistory") or []
        lookback = int(number(condition.get("keyLevelLookbackBars")) or KEY_LEVEL_HAMMER_LOOKBACK_DEFAULT)
        lookback = max(1, min(lookback, KEY_LEVEL_HAMMER_LOOKBACK_MAX))
        threshold = number(condition.get("keyLevelThreshold"))
        threshold = KEY_LEVEL_SIGNAL_THRESHOLDS["resistance_inverted_hammer_rejection"] if threshold is None else threshold
        return float(any(
            isinstance(item, dict)
            and item.get("event")
            and number(item.get("resistanceScore")) is not None
            and number(item.get("resistanceScore")) >= threshold
            for item in history[-lookback:]
        ))
    if indicator in KEY_LEVEL_FEATURES:
        return number(metrics.get(indicator))
    period = max(1, min(int(number(condition.get("period")) or DEFAULT_INDICATOR_PERIOD), 200))
    deviations = max(0.1, min(number(condition.get("deviations")) or 2, 10))
    return indicator_value(indicator, closes, period, deviations)


def compare(left: float | None, operator: str, right: float | None) -> bool:
    if left is None or right is None:
        return False
    return {
        ">": left > right,
        ">=": left >= right,
        "<": left < right,
        "<=": left <= right,
        "=": math.isclose(left, right, rel_tol=1e-9, abs_tol=1e-12),
        "!=": not math.isclose(left, right, rel_tol=1e-9, abs_tol=1e-12),
    }.get(operator, False)


def evaluate_condition(condition: dict[str, Any], metrics: dict[str, Any], closes: list[float]) -> bool:
    left = resolve_indicator(condition, metrics, closes)
    if condition.get("valueType") == "indicator":
        right = resolve_indicator(
            {
                "indicator": condition.get("valueIndicator", "price"),
                "period": condition.get("valuePeriod", DEFAULT_INDICATOR_PERIOD),
                "deviations": condition.get("valueDeviations", 2),
            },
            metrics,
            closes,
        )
    elif condition.get("valueType") == "boolean":
        right = 1.0 if condition.get("booleanValue") else 0.0
    else:
        right = number(condition.get("value"))
    operator = str(condition.get("operator", ">"))
    if operator == "within_percent":
        tolerance = number(condition.get("value"))
        if left is None or right is None or tolerance is None or abs(right) <= 1e-12:
            return False
        distance_percent = abs(left - right) / abs(right) * 100
        return distance_percent <= tolerance or math.isclose(
            distance_percent, tolerance, rel_tol=1e-9, abs_tol=1e-12,
        )
    return compare(left, operator, right)


def condition_timeframe(condition: dict[str, Any], default: str) -> str:
    timeframe = str(condition.get("timeframe") or default)
    if timeframe not in TIMEFRAMES:
        raise ValueError(f"不支持的条件周期：{timeframe}")
    return timeframe


def screening_condition_label(condition: dict[str, Any], default_timeframe: str) -> str:
    timeframe = condition_timeframe(condition, default_timeframe)
    if condition.get("indicator") == "key_levels":
        signal_names = {
            "support_exists": "存在支撑区域", "resistance_exists": "存在压力区域",
            "high_quality_support": "高质量支撑", "high_quality_resistance": "高质量压力",
            "support_near": "价格接近支撑", "resistance_near": "价格接近压力",
            "support_hammer_rejection": "支撑锤型线拒绝",
            "resistance_inverted_hammer_rejection": "压力位长上影拒绝",
            "support_touch": "收盘触碰支撑", "resistance_touch": "收盘触碰压力",
            "support_break": "收盘跌破支撑", "resistance_break": "收盘突破压力",
            "support_retest": "支撑回踩确认", "resistance_retest": "压力回踩确认",
        }
        signal = str(condition.get("keyLevelSignal", "关键位条件"))
        label = signal_names.get(signal, signal)
        threshold = number(condition.get("keyLevelThreshold"))
        if threshold is not None and signal in KEY_LEVEL_SIGNAL_THRESHOLDS:
            label += f" ≥ {threshold:g}" if not signal.endswith("_near") else f" ≤ {threshold:g} ATR"
        return f"{label} · {timeframe}"

    names = {
        "price": "价格", "change24h": "24h涨跌幅", "volume24h": "24h成交额",
        "ma": "MA", "ema": "EMA", "rsi": "RSI", "bb_upper": "布林上轨",
        "bb_middle": "布林中轨", "bb_lower": "布林下轨", "bb_width": "布林带宽",
    }

    def indicator_label(name: str, period: Any) -> str:
        label = names.get(name, KEY_LEVEL_FEATURES.get(name, name))
        return f"{label}{period}" if name in {"ma", "ema", "rsi", "bb_upper", "bb_middle", "bb_lower", "bb_width"} else label

    left = indicator_label(str(condition.get("indicator", "price")), condition.get("period"))
    operator = str(condition.get("operator", ">"))
    if operator == "within_percent":
        right = indicator_label(str(condition.get("valueIndicator", "price")), condition.get("valuePeriod"))
        return f"{left} 距 {right} ≤ {number(condition.get('value')) or 0:g}% · {timeframe}"
    if condition.get("valueType") == "indicator":
        right = indicator_label(str(condition.get("valueIndicator", "price")), condition.get("valuePeriod"))
    elif condition.get("valueType") == "boolean":
        right = "是" if condition.get("booleanValue") else "否"
    else:
        right = f"{number(condition.get('value')):g}" if number(condition.get("value")) is not None else "未填写"
    operator_label = {">=": "≥", "<=": "≤", "=": "=", "!=": "≠"}.get(operator, operator)
    return f"{left} {operator_label} {right} · {timeframe}"


def condition_has_missing_data(condition: dict[str, Any], metrics: dict[str, Any],
                               closes: list[float]) -> bool:
    if resolve_indicator(condition, metrics, closes) is None:
        return True
    if condition.get("valueType") == "indicator":
        right = {
            "indicator": condition.get("valueIndicator", "price"),
            "period": condition.get("valuePeriod", DEFAULT_INDICATOR_PERIOD),
            "deviations": condition.get("valueDeviations", 2),
        }
        return resolve_indicator(right, metrics, closes) is None
    return False


def condition_period(condition: dict[str, Any]) -> int:
    indicators = {str(condition.get("indicator", "price")), str(condition.get("valueIndicator", "price"))}
    if indicators & KEY_LEVEL_DYNAMIC_FEATURES:
        return KEY_LEVEL_BASE_HISTORY - 30
    left = int(number(condition.get("period")) or DEFAULT_INDICATOR_PERIOD)
    right = int(number(condition.get("valuePeriod")) or DEFAULT_INDICATOR_PERIOD)
    period = max(1, min(200, max(left, right)))
    return min(MAX_CANDLES - 30, max(MIN_INDICATOR_HISTORY, period * INDICATOR_HISTORY_MULTIPLIER) - 30)


def needs_candles(condition: dict[str, Any]) -> bool:
    indicators = {str(condition.get("indicator", "price"))}
    if condition.get("valueType") == "indicator":
        indicators.add(str(condition.get("valueIndicator", "price")))
    return bool(indicators - {"change24h", "volume24h"})


def passes_ticker_filters(market: dict[str, Any], ticker: dict[str, Any], conditions: list[dict[str, Any]]) -> bool:
    metrics = {
        "change24h": ticker.get("change24h"),
        "volume24h": ticker.get("volume24h"),
    }
    for condition in conditions:
        indicator = str(condition.get("indicator", "price"))
        if indicator not in metrics or condition.get("valueType") == "indicator":
            continue
        if not compare(number(metrics[indicator]), str(condition.get("operator", ">")), number(condition.get("value"))):
            return False
    return True


def validate_conditions(conditions: list[dict[str, Any]]) -> None:
    for index, condition in enumerate(conditions, start=1):
        indicator = str(condition.get("indicator", "price"))
        operator = str(condition.get("operator", ">"))
        if operator == "within_percent":
            right_indicator = str(condition.get("valueIndicator", ""))
            if (indicator != "price" or condition.get("valueType") != "indicator"
                    or right_indicator not in PRICE_SCALE_INDICATORS):
                raise ValueError(
                    f"第 {index} 条距离条件需要使用最新价，并选择 MA、EMA 或布林轨道指标。"
                )
            tolerance = number(condition.get("value"))
            if tolerance is None or tolerance < 0 or tolerance > 100:
                raise ValueError(f"第 {index} 条指标距离范围应在 0 到 100% 之间。")
            continue
        if indicator == "key_levels":
            signal = str(condition.get("keyLevelSignal", ""))
            if signal not in KEY_LEVEL_SIGNALS:
                raise ValueError(f"第 {index} 条条件请选择有效的关键位信号。")
            if signal in KEY_LEVEL_SIGNAL_THRESHOLDS:
                threshold = number(condition.get("keyLevelThreshold"))
                maximum = 10 if signal.endswith("_near") else 100
                if threshold is None or threshold < 0 or threshold > maximum:
                    raise ValueError(f"第 {index} 条关键位阈值应在 0 到 {maximum} 之间。")
            if signal in {"support_hammer_rejection", "resistance_inverted_hammer_rejection"}:
                lookback = number(condition.get("keyLevelLookbackBars"))
                if lookback is not None and (
                    lookback != int(lookback) or lookback < 1 or lookback > KEY_LEVEL_HAMMER_LOOKBACK_MAX
                ):
                    raise ValueError(
                        f"第 {index} 条裸K形态回看根数应为 1 到 {KEY_LEVEL_HAMMER_LOOKBACK_MAX} 的整数。"
                    )
            continue
        is_binary = indicator in KEY_LEVEL_BINARY_FEATURES
        if is_binary:
            if condition.get("valueType") != "boolean":
                raise ValueError(f"第 {index} 条条件是状态指标，请将右侧类型设为“状态”。")
            if str(condition.get("operator", "=")) not in {"=", "!="}:
                raise ValueError(f"第 {index} 条条件是状态指标，只能使用“等于”或“不等于”。")
            continue
        if condition.get("valueType") == "boolean":
            raise ValueError(f"第 {index} 条条件不是状态指标，请将右侧类型设为“数值”或“指标”。")
        if condition.get("valueType") != "indicator":
            continue
        left_indicator = str(condition.get("indicator", "price"))
        right_indicator = str(condition.get("valueIndicator", "price"))
        left_period = int(number(condition.get("period")) or DEFAULT_INDICATOR_PERIOD)
        right_period = int(number(condition.get("valuePeriod")) or DEFAULT_INDICATOR_PERIOD)
        same_series = left_indicator == right_indicator and (
            left_indicator in KEY_LEVEL_FEATURES or left_period == right_period
        )
        operator = str(condition.get("operator", ">"))
        if same_series and operator in {">", "<", "!=", "=", ">=", "<="}:
            indicator_names = {
                "price": "最新价",
                "ma": "MA",
                "ema": "EMA",
                "rsi": "RSI",
                "bb_upper": "布林上轨",
                "bb_middle": "布林中轨",
                "bb_lower": "布林下轨",
            }
            label = f"{indicator_names.get(left_indicator, left_indicator)}({left_period})"
            if operator in {">", "<", "!="}:
                raise ValueError(f"第 {index} 条条件无效：{label} 与 {label} 是同一个值，使用“不等于/大于/小于”永远不会命中。请修改右侧指标周期。")
            raise ValueError(f"第 {index} 条条件恒为真：{label} 与 {label} 相同，不需要设置这条条件。")


def expand_key_level_conditions(conditions: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """Translate the single user-facing Pine condition into internal feature checks."""
    expanded = []
    for condition in conditions:
        if str(condition.get("indicator", "price")) != "key_levels":
            expanded.append(condition)
            continue
        timeframe = condition.get("timeframe", "1h")
        signal = str(condition.get("keyLevelSignal"))
        threshold = number(condition.get("keyLevelThreshold"))
        if threshold is None:
            threshold = KEY_LEVEL_SIGNAL_THRESHOLDS.get(signal, 0.0)

        def boolean(metric: str) -> dict[str, Any]:
            return {
                "timeframe": timeframe, "indicator": metric, "operator": "=",
                "valueType": "boolean", "booleanValue": True,
            }

        def numeric(metric: str, operator: str, value: float) -> dict[str, Any]:
            return {
                "timeframe": timeframe, "indicator": metric, "operator": operator,
                "valueType": "number", "value": value,
            }

        pattern_lookback = number(condition.get("keyLevelLookbackBars"))
        if pattern_lookback is None:
            pattern_lookback = KEY_LEVEL_HAMMER_LOOKBACK_DEFAULT
        hammer_event = boolean(KEY_LEVEL_RECENT_HAMMER_FEATURE)
        hammer_event["keyLevelLookbackBars"] = int(pattern_lookback)
        hammer_event["keyLevelThreshold"] = threshold
        inverted_hammer_event = boolean(KEY_LEVEL_RECENT_INVERTED_HAMMER_FEATURE)
        inverted_hammer_event["keyLevelLookbackBars"] = int(pattern_lookback)
        inverted_hammer_event["keyLevelThreshold"] = threshold

        rules: dict[str, list[dict[str, Any]]] = {
            "support_exists": [boolean("kl_support_exists")],
            "resistance_exists": [boolean("kl_resistance_exists")],
            "high_quality_support": [
                boolean("kl_support_exists"), numeric("kl_support_score", ">=", threshold),
            ],
            "high_quality_resistance": [
                boolean("kl_resistance_exists"), numeric("kl_resistance_score", ">=", threshold),
            ],
            "support_hammer_rejection": [hammer_event],
            "resistance_inverted_hammer_rejection": [inverted_hammer_event],
            "support_near": [
                boolean("kl_support_exists"), numeric("kl_support_distance_atr", "<=", threshold),
            ],
            "resistance_near": [
                boolean("kl_resistance_exists"), numeric("kl_resistance_distance_atr", "<=", threshold),
            ],
            "support_touch": [boolean("kl_event_support_touch")],
            "resistance_touch": [boolean("kl_event_resistance_touch")],
            "support_break": [boolean("kl_event_support_break")],
            "resistance_break": [boolean("kl_event_resistance_break")],
            "support_retest": [boolean("kl_event_support_retest")],
            "resistance_retest": [boolean("kl_event_resistance_retest")],
        }
        reference_comparisons = {
            "price_above_vwap": ("kl_session_vwap", ">"),
            "price_below_vwap": ("kl_session_vwap", "<"),
            "price_above_previous_day_high": ("kl_previous_day_high", ">"),
            "price_below_previous_day_low": ("kl_previous_day_low", "<"),
            "price_above_previous_week_high": ("kl_previous_week_high", ">"),
            "price_below_previous_week_low": ("kl_previous_week_low", "<"),
            "price_above_day_open": ("kl_day_open", ">"),
            "price_below_day_open": ("kl_day_open", "<"),
        }
        if signal in reference_comparisons:
            reference, operator = reference_comparisons[signal]
            rules[signal] = [{
                "timeframe": timeframe, "indicator": "price", "operator": operator,
                "valueType": "indicator", "valueIndicator": reference,
            }]
        expanded.extend(rules[signal])
    return expanded


def rank_price_changes(payload: dict[str, Any]) -> dict[str, Any]:
    started_at = time.perf_counter()
    timeframe = str(payload.get("timeframe", "1h"))
    if timeframe not in TIMEFRAMES:
        raise ValueError("不支持的涨跌幅周期")
    quote = str(payload.get("quote", "USDT")).upper()
    all_markets = [market for market in fetch_markets() if market["quote"] == quote]
    tickers = fetch_tickers()
    requested = [item.strip().upper() for item in str(payload.get("symbols", "")).split(",") if item.strip()]
    if requested:
        requested_set = set(requested)
        markets = [market for market in all_markets if market["symbol"].upper() in requested_set or market["id"].upper() in requested_set]
    else:
        markets = list(all_markets)
    markets.sort(key=lambda market: tickers.get(market["id"], {}).get("volume24h") or 0, reverse=True)
    max_symbols = max(1, min(int(number(payload.get("maxSymbols")) or 100), len(markets) or 1))
    if not requested:
        markets = markets[:max_symbols]
    direction = "asc" if str(payload.get("direction", "desc")) == "asc" else "desc"

    def load_change(market: dict[str, Any]) -> tuple[dict[str, Any] | None, str | None]:
        try:
            candles = fetch_candles(market["id"], timeframe, required_period=1, limit_override=3)
            ticker = tickers.get(market["id"], {})
            if len(candles) < 2:
                return None, f"{market['symbol']} {timeframe}: K 线数量不足"
            reference = candles[-2][4]
            last_close = candles[-1][4]
            current = ticker.get("last") or last_close
            if reference is None or reference == 0 or current is None:
                return None, f"{market['symbol']} {timeframe}: 缺少价格"
            return {
                "symbol": market["symbol"],
                "instId": market["id"],
                "quote": market["quote"],
                "assetType": market.get("assetType"),
                **market_trade_navigation(market),
                "price": format_value(current),
                "periodStartPrice": format_value(reference),
                "lastClosePrice": format_value(last_close),
                "changePercent": format_value((current - reference) / reference * 100),
                "closeChangePercent": format_value((last_close - reference) / reference * 100),
                "volume24h": format_value(ticker.get("volume24h")),
                "change24h": format_value(ticker.get("change24h")),
                "candles": len(candles),
                "timestamp": ticker.get("timestamp"),
            }, None
        except DeepcoinError as exc:
            return None, f"{market['symbol']} {timeframe}: {exc}"

    results: list[dict[str, Any]] = []
    errors: list[str] = []
    with ThreadPoolExecutor(max_workers=min(MAX_WORKERS, max(1, len(markets)))) as executor:
        futures = [executor.submit(load_change, market) for market in markets]
        for future in as_completed(futures):
            result, error = future.result()
            if result:
                results.append(result)
            elif error and len(errors) < 3:
                errors.append(error)
    results.sort(key=lambda item: item.get("changePercent") or 0, reverse=direction == "desc")
    for index, result in enumerate(results, start=1):
        result["rank"] = index
    return {
        "results": results,
        "scanned": len(markets),
        "totalMarkets": len(all_markets),
        "timeframe": timeframe,
        "direction": direction,
        "failed": len(markets) - len(results),
        "errors": errors,
        "websiteCatalog": website_catalog_health(),
        "durationMs": round((time.perf_counter() - started_at) * 1000),
        "updatedAt": int(time.time() * 1000),
    }


def cached_rank_price_changes(payload: dict[str, Any], max_age_seconds: int = 0) -> dict[str, Any]:
    """Share matching automatic rank refreshes across the panel and carousel window."""
    max_age_seconds = max(0, min(3600, int(max_age_seconds or 0)))
    key = (
        str(payload.get("quote", "USDT")).upper(),
        str(payload.get("timeframe", "1h")),
        "asc" if str(payload.get("direction", "desc")) == "asc" else "desc",
        max(1, min(1000, int(number(payload.get("maxSymbols")) or 100))),
        str(payload.get("symbols", "")).strip().upper(),
    )
    with _rank_result_guard:
        key_lock = _rank_result_locks.setdefault(key, threading.Lock())
    with key_lock:
        if max_age_seconds:
            with _rank_result_guard:
                cached = _rank_result_cache.get(key)
                if cached and time.monotonic() - cached[0] < max_age_seconds:
                    return deepcopy(cached[1])
        result = rank_price_changes(payload)
        with _rank_result_guard:
            if result.get("results"):
                _rank_result_cache[key] = (time.monotonic(), deepcopy(result))
                if len(_rank_result_cache) > 128:
                    oldest = min(_rank_result_cache, key=lambda item: _rank_result_cache[item][0])
                    _rank_result_cache.pop(oldest, None)
            else:
                _rank_result_cache.pop(key, None)
        return result


def screen(payload: dict[str, Any]) -> dict[str, Any]:
    started_at = time.perf_counter()
    display_timeframe = str(payload.get("timeframe", "1h"))
    if display_timeframe not in TIMEFRAMES:
        raise ValueError("不支持的 K 线周期")
    quote = str(payload.get("quote", "USDT")).upper()
    user_conditions = payload.get("conditions") or []
    if not isinstance(user_conditions, list) or len(user_conditions) > 12:
        raise ValueError("筛选条件最多 12 条")
    validate_conditions(user_conditions)
    condition_groups = [expand_key_level_conditions([condition]) for condition in user_conditions]
    conditions = [condition for group in condition_groups for condition in group]
    all_markets = [market for market in fetch_markets() if market["quote"] == quote]
    markets = list(all_markets)
    tickers = fetch_tickers()
    requested = [item.strip().upper() for item in str(payload.get("symbols", "")).split(",") if item.strip()]
    if requested:
        requested_set = set(requested)
        markets = [market for market in markets if market["symbol"].upper() in requested_set or market["id"].upper() in requested_set]
    # Ranking by 24h quote volume keeps an all-market scan responsive while still allowing explicit symbols.
    markets.sort(key=lambda market: tickers.get(market["id"], {}).get("volume24h") or 0, reverse=True)
    max_symbols = max(1, min(int(number(payload.get("maxSymbols")) or 50), len(markets) or 1))
    # An explicit symbol list is an exact request; the scan limit only limits an all-market scan.
    if not requested:
        markets = markets[:max_symbols]
    screening_diagnostics = {
        "selectedMarkets": len(markets),
        "tickerFiltered": 0,
        "candleCandidates": 0,
        "close": {"matched": 0, "conditionsNotMet": 0, "missingData": 0},
        "live": {"matched": 0, "conditionsNotMet": 0, "missingData": 0},
        "conditions": [
            {
                "label": screening_condition_label(condition, display_timeframe),
                "close": {"passed": 0, "notMet": 0, "missing": 0},
                "live": {"passed": 0, "notMet": 0, "missing": 0},
            }
            for condition in user_conditions
        ],
    }
    if not markets:
        return {"closeResults": [], "liveResults": [], "results": [], "scanned": 0, "totalMarkets": len(all_markets), "timeframe": display_timeframe, "timeframes": [], "screeningDiagnostics": screening_diagnostics, "updatedAt": int(time.time() * 1000)}

    # Simple 24h filters can reject symbols without downloading any candles.
    candidate_markets = [market for market in markets if passes_ticker_filters(market, tickers.get(market["id"], {}), conditions)]
    screening_diagnostics["tickerFiltered"] = len(markets) - len(candidate_markets)
    screening_diagnostics["candleCandidates"] = len(candidate_markets)

    # Closed candles drive screening; short current-inclusive windows serve reference levels.
    condition_timeframes = {condition_timeframe(condition, display_timeframe) for condition in conditions}
    key_level_timeframes = {
        condition_timeframe(condition, display_timeframe)
        for condition in conditions
        if str(condition.get("indicator", "price")) in KEY_LEVEL_FEATURES
        or (condition.get("valueType") == "indicator" and str(condition.get("valueIndicator", "price")) in KEY_LEVEL_FEATURES)
    }
    dynamic_key_level_timeframes = {
        condition_timeframe(condition, display_timeframe)
        for condition in conditions
        if str(condition.get("indicator", "price")) in KEY_LEVEL_DYNAMIC_FEATURES
        or (condition.get("valueType") == "indicator" and str(condition.get("valueIndicator", "price")) in KEY_LEVEL_DYNAMIC_FEATURES)
    }
    reference_key_level_timeframes = {
        condition_timeframe(condition, display_timeframe)
        for condition in conditions
        if str(condition.get("indicator", "price")) in KEY_LEVEL_REFERENCE_FEATURES
        or (condition.get("valueType") == "indicator" and str(condition.get("valueIndicator", "price")) in KEY_LEVEL_REFERENCE_FEATURES)
    }
    vwap_timeframes = {
        condition_timeframe(condition, display_timeframe)
        for condition in conditions
        if condition.get("indicator") == "kl_session_vwap"
        or (condition.get("valueType") == "indicator" and condition.get("valueIndicator") == "kl_session_vwap")
    }
    # Pure ticker conditions (24h change / volume) do not need a K-line request.
    required_timeframes = {
        condition_timeframe(condition, display_timeframe)
        for condition in conditions
        if needs_candles(condition)
    }
    if dynamic_key_level_timeframes:
        required_timeframes.update(dynamic_key_level_timeframes)
        required_timeframes.add("4h")
    periods_by_timeframe = {}
    for timeframe in required_timeframes:
        periods = [condition_period(condition) for condition in conditions
                   if condition_timeframe(condition, display_timeframe) == timeframe]
        periods_by_timeframe[timeframe] = max(periods or [DEFAULT_INDICATOR_PERIOD])
        if timeframe in dynamic_key_level_timeframes:
            periods_by_timeframe[timeframe] = max(periods_by_timeframe[timeframe], KEY_LEVEL_BASE_HISTORY - 30)
        if timeframe in vwap_timeframes:
            periods_by_timeframe[timeframe] = max(
                periods_by_timeframe[timeframe],
                math.ceil(86_400_000 / TIMEFRAMES[timeframe]) + 2,
            )
        if timeframe == "4h" and dynamic_key_level_timeframes:
            htf_periods = [
                min(MAX_CANDLES - 30, max(1,
                    math.ceil(int(KEY_LEVEL_SETTINGS["maxZoneAge"]) * TIMEFRAMES[base_timeframe] / TIMEFRAMES["4h"])
                    + KEY_LEVEL_HTF_WARMUP - 30))
                for base_timeframe in dynamic_key_level_timeframes
            ]
            periods_by_timeframe[timeframe] = max(periods_by_timeframe[timeframe], *htf_periods)
    candle_sets: dict[tuple[str, str], list[list[float]]] = {}
    recent_candle_sets: dict[tuple[str, str], list[list[float]]] = {}
    failed_candles = 0
    candle_jobs = 0
    candle_errors: list[str] = []

    def load_candles(market: dict[str, Any], timeframe: str) -> tuple[tuple[str, str], list[list[float]] | None, str | None]:
        try:
            return (market["id"], timeframe), fetch_candles(market["id"], timeframe, periods_by_timeframe[timeframe]), None
        except DeepcoinError as exc:
            return (market["id"], timeframe), None, f"{market['symbol']} {timeframe}: {exc}"

    def load_recent(market: dict[str, Any], timeframe: str) -> tuple[tuple[str, str], list[list[float]] | None, str | None]:
        try:
            limit = 20 if timeframe == "1d" else 5 if timeframe == "1w" else 3
            return (market["id"], timeframe), fetch_recent_candles(market["id"], timeframe, limit), None
        except DeepcoinError as exc:
            return (market["id"], timeframe), None, f"{market['symbol']} {timeframe}: {exc}"

    jobs = [(market, timeframe) for market in candidate_markets for timeframe in required_timeframes]
    recent_timeframes = (reference_key_level_timeframes | {"1d", "1w"}) if reference_key_level_timeframes else set()
    recent_jobs = [(market, timeframe) for market in candidate_markets for timeframe in recent_timeframes]
    candle_jobs = len(jobs) + len(recent_jobs)
    with ThreadPoolExecutor(max_workers=min(MAX_WORKERS, max(1, len(jobs) + len(recent_jobs)))) as executor:
        futures = [(executor.submit(load_candles, market, timeframe), False) for market, timeframe in jobs]
        futures.extend((executor.submit(load_recent, market, timeframe), True) for market, timeframe in recent_jobs)
        future_kinds = {future: is_recent for future, is_recent in futures}
        for future in as_completed(future_kinds):
            is_recent = future_kinds[future]
            key, candles, error = future.result()
            if candles is None:
                failed_candles += 1
                if error and len(candle_errors) < 3:
                    candle_errors.append(error)
            elif is_recent:
                recent_candle_sets[key] = candles
            else:
                candle_sets[key] = candles

    close_results = []
    live_results = []
    for market in candidate_markets:
        ticker = tickers.get(market["id"], {})
        close_metrics_by_tf: dict[str, dict[str, Any]] = {}
        live_metrics_by_tf: dict[str, dict[str, Any]] = {}
        closes_by_tf: dict[str, list[float]] = {}
        live_closes_by_tf: dict[str, list[float]] = {}
        key_level_features_by_tf: dict[str, dict[str, float | None]] = {}
        live_key_level_features_by_tf: dict[str, dict[str, float | None]] = {}
        key_level_snapshots: dict[str, dict[str, Any] | None] = {}
        for timeframe in key_level_timeframes:
            base_bars = candle_sets.get((market["id"], timeframe), [])
            snapshot = None
            if timeframe in dynamic_key_level_timeframes:
                htf_bars = base_bars if timeframe == "4h" else candle_sets.get((market["id"], "4h"), [])
                snapshot = cached_key_level_snapshot(
                    market["id"], timeframe,
                    base_bars,
                    htf_bars,
                    TIMEFRAMES[timeframe],
                    TIMEFRAMES["4h"],
                    number(market.get("tickSize")) or 0.0,
                )
            key_level_snapshots[timeframe] = snapshot
            close_price = base_bars[-1][4] if base_bars else None
            close_references = None
            live_references = None
            if timeframe in reference_key_level_timeframes:
                daily_bars = recent_candle_sets.get((market["id"], "1d"), [])
                weekly_bars = recent_candle_sets.get((market["id"], "1w"), [])
                recent_base = recent_candle_sets.get((market["id"], timeframe), [])
                live_bars_by_timestamp = {int(row[0]): row for row in base_bars}
                live_bars_by_timestamp.update({int(row[0]): row for row in recent_base})
                live_base_bars = [live_bars_by_timestamp[key] for key in sorted(live_bars_by_timestamp)]
                close_references = calculate_reference_levels(
                    base_bars, daily_bars, weekly_bars, TIMEFRAMES[timeframe]
                )
                live_references = calculate_reference_levels(
                    live_base_bars, daily_bars, weekly_bars, TIMEFRAMES[timeframe]
                )
            key_level_features_by_tf[timeframe] = encode_conditions(
                snapshot, close_price, references=close_references
            )
            live_key_level_features_by_tf[timeframe] = encode_conditions(
                snapshot, ticker.get("last"), live=True, references=live_references
            )

        for timeframe in required_timeframes:
            candles = candle_sets.get((market["id"], timeframe), [])
            closes = [row[4] for row in candles]
            closes_by_tf[timeframe] = closes
            live_closes_by_tf[timeframe] = closes + ([ticker["last"]] if ticker.get("last") is not None else [])
            close_features = key_level_features_by_tf.get(timeframe)
            live_features = live_key_level_features_by_tf.get(timeframe)
            close_metrics_by_tf[timeframe] = make_metrics(market, ticker, candles, live=False, key_level_features=close_features)
            live_metrics_by_tf[timeframe] = make_metrics(market, ticker, candles, live=True, key_level_features=live_features)
            if timeframe in key_level_timeframes:
                snapshot = key_level_snapshots.get(timeframe)
                hammer_history = snapshot.get("supportHammerHistory") if snapshot else None
                if hammer_history is None and snapshot:
                    hammer_history = [{
                        "event": bool((snapshot.get("events") or {}).get("supportHammer")),
                        "supportScore": (close_features or {}).get("kl_support_score"),
                    }]
                close_metrics_by_tf[timeframe]["_supportHammerHistory"] = list(hammer_history or [])
                live_metrics_by_tf[timeframe]["_supportHammerHistory"] = []
                inverted_hammer_history = snapshot.get("resistanceInvertedHammerHistory") if snapshot else None
                if inverted_hammer_history is None and snapshot:
                    inverted_hammer_history = [{
                        "event": bool((snapshot.get("events") or {}).get("resistanceInvertedHammer")),
                        "resistanceScore": (close_features or {}).get("kl_resistance_score"),
                    }]
                close_metrics_by_tf[timeframe]["_resistanceInvertedHammerHistory"] = list(
                    inverted_hammer_history or []
                )
                live_metrics_by_tf[timeframe]["_resistanceInvertedHammerHistory"] = []
        def metrics_for(timeframe: str, live: bool) -> tuple[dict[str, Any], list[float]]:
            if timeframe in (live_metrics_by_tf if live else close_metrics_by_tf):
                return (live_metrics_by_tf if live else close_metrics_by_tf)[timeframe], (live_closes_by_tf if live else closes_by_tf)[timeframe]
            return make_metrics(market, ticker, [], live=live), []

        def evaluate_groups(live: bool) -> list[str]:
            mode = "live" if live else "close"
            states = []
            for condition, group, stats in zip(
                user_conditions, condition_groups, screening_diagnostics["conditions"]
            ):
                state = "passed"
                for rule in group:
                    metrics, closes = metrics_for(condition_timeframe(rule, display_timeframe), live)
                    if condition_has_missing_data(rule, metrics, closes):
                        state = "missing"
                        break
                    if not evaluate_condition(rule, metrics, closes):
                        state = "notMet"
                        break
                stats[mode][state] += 1
                states.append(state)
            return states

        close_states = evaluate_groups(False)
        live_states = evaluate_groups(True)
        close_ok = all(state == "passed" for state in close_states)
        live_ok = all(state == "passed" for state in live_states)
        for mode, states, matched in (
            ("close", close_states, close_ok),
            ("live", live_states, live_ok),
        ):
            if matched:
                screening_diagnostics[mode]["matched"] += 1
            elif "missing" in states:
                screening_diagnostics[mode]["missingData"] += 1
            else:
                screening_diagnostics[mode]["conditionsNotMet"] += 1
        display_close = close_metrics_by_tf.get(display_timeframe) or make_metrics(market, ticker, [], live=False)
        display_live = live_metrics_by_tf.get(display_timeframe) or make_metrics(market, ticker, [], live=True)
        common = {key: value for key, value in display_close.items() if not key.startswith("_")}
        common["livePrice"] = display_live.get("livePrice")
        common["closePrice"] = display_close.get("closePrice")
        common["timeframes"] = sorted(condition_timeframes)
        common["candles"] = sum(len(candle_sets.get((market["id"], timeframe), [])) for timeframe in required_timeframes)
        if close_ok:
            close_result = dict(common)
            close_result["price"] = display_close.get("closePrice") or display_close.get("livePrice")
            close_result["mode"] = "close"
            close_result["keyLevelsByTimeframe"] = {
                timeframe: key_level_features_by_tf[timeframe]
                for timeframe in sorted(key_level_features_by_tf)
            }
            close_results.append(close_result)
        if live_ok:
            live_result = dict(common)
            live_result["price"] = display_live.get("livePrice")
            live_result["ema20"] = display_live.get("ema20")
            live_result["ma20"] = display_live.get("ma20")
            live_result["bbUpper"] = display_live.get("bbUpper")
            live_result["bbMiddle"] = display_live.get("bbMiddle")
            live_result["bbLower"] = display_live.get("bbLower")
            live_result["bbWidth"] = display_live.get("bbWidth")
            live_result["mode"] = "live"
            live_result.update({
                name: display_live[name]
                for name in KEY_LEVEL_FEATURES
                if name in display_live
            })
            live_result["keyLevelsByTimeframe"] = {
                timeframe: live_key_level_features_by_tf[timeframe]
                for timeframe in sorted(key_level_snapshots)
            }
            live_results.append(live_result)
    close_results.sort(key=lambda item: item.get("volume24h") or 0, reverse=True)
    live_results.sort(key=lambda item: item.get("volume24h") or 0, reverse=True)
    return {
        "closeResults": close_results,
        "liveResults": live_results,
        "results": close_results,
        "scanned": len(markets),
        "candleCandidates": len(candidate_markets),
        "totalMarkets": len(all_markets),
        "timeframe": display_timeframe,
        "timeframes": sorted(condition_timeframes or {display_timeframe}),
        "failedCandles": failed_candles,
        "candleErrors": candle_errors,
        "websiteCatalog": website_catalog_health(),
        "durationMs": round((time.perf_counter() - started_at) * 1000),
        "candleJobs": candle_jobs,
        "screeningDiagnostics": screening_diagnostics,
        "updatedAt": int(time.time() * 1000),
    }


def indicator_series(name: str, values: list[float], period: int, deviations: float = 2) -> list[float | None]:
    """Build historical values with the same seeding and window rules used by screening."""
    result: list[float | None] = [None] * len(values)
    if period <= 0 or not values:
        return result
    if name == "ma":
        if len(values) < period:
            return result
        rolling = sum(values[:period])
        result[period - 1] = rolling / period
        for index in range(period, len(values)):
            rolling += values[index] - values[index - period]
            result[index] = rolling / period
        return result
    if name == "ema":
        if len(values) < period:
            return result
        current = sum(values[:period]) / period
        result[period - 1] = current
        multiplier = 2 / (period + 1)
        for index in range(period, len(values)):
            current = (values[index] - current) * multiplier + current
            result[index] = current
        return result
    if name == "rsi":
        if len(values) <= period:
            return result
        changes = [values[index] - values[index - 1] for index in range(1, len(values))]
        gains = [max(change, 0.0) for change in changes]
        losses = [max(-change, 0.0) for change in changes]
        average_gain = sum(gains[:period]) / period
        average_loss = sum(losses[:period]) / period
        result[period] = 100.0 if average_loss == 0 else 100 - 100 / (1 + average_gain / average_loss)
        for index in range(period + 1, len(values)):
            change_index = index - 1
            average_gain = (average_gain * (period - 1) + gains[change_index]) / period
            average_loss = (average_loss * (period - 1) + losses[change_index]) / period
            result[index] = 100.0 if average_loss == 0 else 100 - 100 / (1 + average_gain / average_loss)
        return result
    if name in {"bb_upper", "bb_middle", "bb_lower", "bb_width"} and len(values) >= period:
        for index in range(period - 1, len(values)):
            window = values[index - period + 1:index + 1]
            middle = sum(window) / period
            variance = sum((value - middle) ** 2 for value in window) / period
            width = math.sqrt(variance) * deviations
            result[index] = {
                "bb_upper": middle + width,
                "bb_middle": middle,
                "bb_lower": middle - width,
                "bb_width": (width * 2 / middle * 100) if middle else None,
            }[name]
    return result


def chart_indicator_specs(conditions: list[dict[str, Any]], timeframe: str,
                          chart_indicators: list[dict[str, Any]] | None = None) -> list[dict[str, Any]]:
    specs: dict[tuple[str, int, float], dict[str, Any]] = {}

    def add(name: Any, period: Any, deviations: Any = 2) -> None:
        name = str(name or "")
        if name not in {"ma", "ema", "rsi", "bb_upper", "bb_middle", "bb_lower", "bb_width"}:
            return
        p = max(1, min(int(number(period) or DEFAULT_INDICATOR_PERIOD), 200))
        d = max(0.1, min(number(deviations) or 2, 10))
        key = (name, p, d)
        labels = {
            "ma": f"MA{p}", "ema": f"EMA{p}", "rsi": f"RSI{p}",
            "bb_upper": f"BB{p} 上轨", "bb_middle": f"BB{p} 中轨",
            "bb_lower": f"BB{p} 下轨", "bb_width": f"BB{p} 带宽",
        }
        specs[key] = {"name": name, "period": p, "deviations": d,
                      "title": labels[name], "pane": "indicator" if name in {"rsi", "bb_width"} else "price"}

    for condition in conditions:
        if condition_timeframe(condition, timeframe) != timeframe:
            continue
        add(condition.get("indicator"), condition.get("period"), condition.get("deviations"))
        if condition.get("valueType") == "indicator":
            add(condition.get("valueIndicator"), condition.get("valuePeriod"), condition.get("valueDeviations"))
    for item in chart_indicators or []:
        if not isinstance(item, dict):
            continue
        name = str(item.get("name", item.get("indicator", ""))).strip().lower()
        period = item.get("period", DEFAULT_INDICATOR_PERIOD)
        deviations = item.get("deviations", item.get("stddev", 2))
        if name in {"bb", "bollinger", "bollinger_bands"}:
            for band in ("bb_upper", "bb_middle", "bb_lower"):
                add(band, period, deviations)
        else:
            add(name, period, deviations)
    return list(specs.values())


def validate_chart_indicators(chart_indicators: Any) -> list[dict[str, Any]]:
    if chart_indicators in (None, ""):
        return []
    if not isinstance(chart_indicators, list) or len(chart_indicators) > 12:
        raise ValueError("Chart indicator settings are invalid")
    normalized: list[dict[str, Any]] = []
    for index, item in enumerate(chart_indicators, start=1):
        if not isinstance(item, dict):
            raise ValueError(f"Chart indicator {index} is invalid")
        name = str(item.get("name", item.get("indicator", ""))).strip().lower()
        if name not in {"ma", "ema", "rsi", "bb", "bollinger", "bollinger_bands",
                        "bb_upper", "bb_middle", "bb_lower", "bb_width"}:
            raise ValueError(f"Chart indicator {index} is not supported")
        period_value = number(item.get("period"))
        if period_value is None or period_value != int(period_value) or not 1 <= int(period_value) <= 200:
            raise ValueError(f"Chart indicator {index} period must be an integer from 1 to 200")
        deviation_value = number(item.get("deviations", item.get("stddev", 2)))
        if deviation_value is None or not 0.1 <= deviation_value <= 10:
            raise ValueError(f"Chart indicator {index} deviations must be between 0.1 and 10")
        normalized.append({"name": name, "period": int(period_value), "deviations": deviation_value})
    return normalized


def chart_conditions_for_timeframe(conditions: list[dict[str, Any]], timeframe: str,
                                   close_metrics: dict[str, Any], live_metrics: dict[str, Any],
                                   close_values: list[float], live_values: list[float]) -> list[dict[str, Any]]:
    output = []
    for condition in conditions:
        item = dict(condition)
        item["onChart"] = condition_timeframe(condition, timeframe) == timeframe
        if item["onChart"]:
            if str(condition.get("indicator")) == "key_levels":
                expanded = expand_key_level_conditions([condition])
                item["closePass"] = all(evaluate_condition(rule, close_metrics, close_values) for rule in expanded)
                item["livePass"] = all(evaluate_condition(rule, live_metrics, live_values) for rule in expanded)
            else:
                item["closePass"] = evaluate_condition(condition, close_metrics, close_values)
                item["livePass"] = evaluate_condition(condition, live_metrics, live_values)
            if str(condition.get("indicator")) != "key_levels":
                item["closeValue"] = format_value(resolve_indicator(condition, close_metrics, close_values))
                item["liveValue"] = format_value(resolve_indicator(condition, live_metrics, live_values))
        output.append(item)
    return output


def chart_data(payload: dict[str, Any]) -> dict[str, Any]:
    inst_id = str(payload.get("instId", "")).strip()
    timeframe = str(payload.get("timeframe", "1h"))
    mode = str(payload.get("mode", "close"))
    if timeframe not in TIMEFRAMES:
        raise ValueError("不支持的图表周期")
    if mode not in {"close", "live"}:
        raise ValueError("不支持的图表模式")
    conditions = payload.get("conditions") or []
    if not isinstance(conditions, list) or len(conditions) > 12:
        raise ValueError("图表条件格式无效")
    validate_conditions(conditions)
    chart_indicators = validate_chart_indicators(payload.get("chartIndicators"))

    market = next((item for item in fetch_markets() if item["id"] == inst_id), None)
    if not market:
        raise ValueError("该合约当前未通过 Deepcoin 官网清单核验，无法加载图表")

    matching_conditions = [condition for condition in conditions
                           if condition_timeframe(condition, timeframe) == timeframe]
    key_level_conditions = [condition for condition in matching_conditions
                            if str(condition.get("indicator", "")) == "key_levels"]
    dynamic_signals = {
        "support_exists", "resistance_exists", "high_quality_support", "high_quality_resistance",
        "support_near", "resistance_near", "support_touch", "resistance_touch", "support_break",
        "support_hammer_rejection", "resistance_inverted_hammer_rejection",
        "resistance_break", "support_retest", "resistance_retest",
    }
    draw_key_levels = bool(key_level_conditions)
    dynamic_key_levels = any(str(condition.get("keyLevelSignal", "")) in dynamic_signals
                             for condition in key_level_conditions)
    reference_key_levels = any(str(condition.get("keyLevelSignal", "")) in {
        "price_above_vwap", "price_below_vwap", "price_above_previous_day_high",
        "price_below_previous_day_low", "price_above_previous_week_high",
        "price_below_previous_week_low", "price_above_day_open", "price_below_day_open",
    } for condition in key_level_conditions)

    periods = [
        int(number(condition.get(key)) or DEFAULT_INDICATOR_PERIOD)
        for condition in matching_conditions
        for key in ("period", "valuePeriod")
        if str(condition.get("indicator" if key == "period" else "valueIndicator", "")) in {
            "ma", "ema", "rsi", "bb_upper", "bb_middle", "bb_lower", "bb_width",
        }
    ]
    periods.extend(item["period"] for item in chart_indicators)
    visible_count = MIN_INDICATOR_HISTORY
    candle_count = max(
        MIN_INDICATOR_HISTORY,
        max((period * INDICATOR_HISTORY_MULTIPLIER for period in periods), default=MIN_INDICATOR_HISTORY),
    )
    if dynamic_key_levels:
        candle_count = max(candle_count, KEY_LEVEL_BASE_HISTORY)
    candle_count = min(MAX_CANDLES, candle_count)
    htf_count = None
    if dynamic_key_levels and timeframe != "4h" and KEY_LEVEL_SETTINGS.get("useHtf"):
        htf_count = min(MAX_CANDLES - 30, max(1, math.ceil(
            int(KEY_LEVEL_SETTINGS["maxZoneAge"]) * TIMEFRAMES[timeframe] / TIMEFRAMES["4h"]
        ) + KEY_LEVEL_HTF_WARMUP - 30))
    with ThreadPoolExecutor(max_workers=6) as executor:
        candle_future = executor.submit(
            fetch_candles, inst_id, timeframe, candle_count - 30, candle_count,
        )
        ticker_future = executor.submit(fetch_tickers)
        htf_future = executor.submit(
            fetch_candles, inst_id, "4h", htf_count, min(MAX_CANDLES, htf_count + 30),
        ) if htf_count is not None else None
        daily_future = executor.submit(fetch_recent_candles, inst_id, "1d", 20) if reference_key_levels else None
        weekly_future = executor.submit(fetch_recent_candles, inst_id, "1w", 5) if reference_key_levels else None
        recent_base_future = executor.submit(fetch_recent_candles, inst_id, timeframe, 3) if reference_key_levels else None
        candles = candle_future.result()
        tickers = ticker_future.result()
        htf_bars = htf_future.result() if htf_future else candles if dynamic_key_levels else []
        daily_bars = daily_future.result() if daily_future else []
        weekly_bars = weekly_future.result() if weekly_future else []
        recent_base = recent_base_future.result() if recent_base_future else []
    if not candles:
        raise DeepcoinError(f"{market['symbol']} {timeframe} 暂无可用 K 线")

    ticker = tickers.get(inst_id, {})
    key_level_features_close: dict[str, float | None] = {}
    key_level_features_live: dict[str, float | None] = {}
    snapshots: list[dict[str, Any]] = []
    event_markers: list[dict[str, Any]] = []
    if dynamic_key_levels:
        htf_events = confirmed_htf_events(htf_bars, candles, TIMEFRAMES[timeframe],
                                          TIMEFRAMES["4h"], KEY_LEVEL_SETTINGS)
        calculator = KeyLevelCalculator(tick_size=number(market.get("tickSize")) or 0.0)
        for index, bar in enumerate(candles):
            snapshot = calculator.process([bar], {index: htf_events[index]} if index in htf_events else None)
            if not snapshot:
                continue
            snapshots.append(snapshot)
            events = snapshot.get("events", {})
            for event_key, position, color, shape in (
                ("supportTouch", "belowBar", "#16866d", "circle"),
                ("resistanceTouch", "aboveBar", "#c65e56", "circle"),
                ("supportBroken", "belowBar", "#c65e56", "arrowDown"),
                ("resistanceBroken", "aboveBar", "#16866d", "arrowUp"),
            ):
                if events.get(event_key):
                    event_markers.append({
                        "time": int(bar[0] // 1000), "position": position,
                        "color": color, "shape": shape,
                    })
            if events.get("supportHammer"):
                event_markers.append({
                    "time": int(bar[0] // 1000), "position": "belowBar",
                    "color": "#16866d", "shape": "arrowUp", "text": "锤",
                })
            if events.get("resistanceInvertedHammer"):
                event_markers.append({
                    "time": int(bar[0] // 1000), "position": "aboveBar",
                    "color": "#c65e56", "shape": "arrowDown", "text": "长上影",
                })
            if events.get("retestConfirmed"):
                support_retest = events.get("retestDirection") == 1
                event_markers.append({
                    "time": int(bar[0] // 1000),
                    "position": "belowBar" if support_retest else "aboveBar",
                    "color": "#16866d" if support_retest else "#c65e56",
                    "shape": "arrowUp" if support_retest else "arrowDown",
                })
        latest_snapshot = snapshots[-1] if snapshots else None
        key_level_features_close = encode_conditions(latest_snapshot, candles[-1][4], live=False)
        key_level_features_live = encode_conditions(latest_snapshot, ticker.get("last"), live=True)

    close_references: dict[str, float | None] = {}
    live_references: dict[str, float | None] = {}
    if reference_key_levels:
        live_bars_by_timestamp = {int(row[0]): row for row in candles}
        live_bars_by_timestamp.update({int(row[0]): row for row in recent_base})
        live_base = [live_bars_by_timestamp[key] for key in sorted(live_bars_by_timestamp)]
        close_references = calculate_reference_levels(candles, daily_bars, weekly_bars,
                                                      TIMEFRAMES[timeframe])
        live_references = calculate_reference_levels(live_base, daily_bars, weekly_bars,
                                                     TIMEFRAMES[timeframe])
        key_level_features_close = encode_conditions(
            snapshots[-1] if snapshots else None, candles[-1][4], references=close_references
        ) | key_level_features_close
        key_level_features_live = encode_conditions(
            snapshots[-1] if snapshots else None, ticker.get("last"), live=True,
            references=live_references,
        ) | key_level_features_live

    close_features = make_metrics(market, ticker, candles, live=False,
                                  key_level_features=key_level_features_close)
    live_features = make_metrics(market, ticker, candles, live=True,
                                 key_level_features=key_level_features_live)
    hammer_history = []
    inverted_hammer_history = []
    for index, snapshot in enumerate(snapshots[-KEY_LEVEL_HAMMER_LOOKBACK_MAX:]):
        support_score = snapshot.get("supportHammerScore")
        if support_score is None and index == len(snapshots[-KEY_LEVEL_HAMMER_LOOKBACK_MAX:]) - 1:
            support_score = key_level_features_close.get("kl_support_score")
        hammer_history.append({
            "event": bool((snapshot.get("events") or {}).get("supportHammer")),
            "supportScore": support_score,
        })
        resistance_score = snapshot.get("resistanceLongUpperWickScore")
        if resistance_score is None and index == len(snapshots[-KEY_LEVEL_HAMMER_LOOKBACK_MAX:]) - 1:
            resistance_score = key_level_features_close.get("kl_resistance_score")
        inverted_hammer_history.append({
            "event": bool((snapshot.get("events") or {}).get("resistanceInvertedHammer")),
            "resistanceScore": resistance_score,
        })
    close_features["_supportHammerHistory"] = hammer_history
    live_features["_supportHammerHistory"] = []
    close_features["_resistanceInvertedHammerHistory"] = inverted_hammer_history
    live_features["_resistanceInvertedHammerHistory"] = []
    latest_zones = snapshots[-1].get("zones", []) if snapshots else []
    zone_source_indices = [
        int(zone.get("originBar", zone["bornBar"])) for zone in latest_zones
        if 0 <= int(zone.get("originBar", zone.get("bornBar", -1))) < len(candles)
    ]
    closes = [float(row[4]) for row in candles]
    live_closes = closes + ([float(ticker["last"])] if ticker.get("last") is not None else [])
    specs = chart_indicator_specs(conditions, timeframe, chart_indicators)
    visible_start = max(0, len(candles) - visible_count)
    if zone_source_indices:
        visible_start = min(visible_start, min(zone_source_indices))
    visible_bars = candles[visible_start:]
    visible_times = [int(row[0] // 1000) for row in visible_bars]
    live_time_ms = int(time.time() * 1000)
    duration = TIMEFRAMES[timeframe]
    offset = int(candles[-1][0]) % duration
    current_open = ((live_time_ms - offset) // duration) * duration + offset
    live_time = int(current_open // 1000)
    indicator_plots = []
    for spec in specs:
        history = indicator_series(spec["name"], closes, spec["period"], spec["deviations"])
        live_history = indicator_series(spec["name"], live_closes, spec["period"], spec["deviations"])
        closed_data = [
            {"time": visible_times[index - visible_start], "value": format_value(value)}
            for index, value in enumerate(history[visible_start:], start=visible_start)
            if value is not None
        ]
        live_data = list(closed_data)
        if ticker.get("last") is not None and live_history and live_history[-1] is not None:
            point = {"time": live_time, "value": format_value(live_history[-1])}
            if live_data and live_data[-1]["time"] == live_time:
                live_data[-1] = point
            else:
                live_data.append(point)
        indicator_plots.append({**spec, "closed": closed_data, "live": live_data})

    close_price = candles[-1][4]
    live_price = ticker.get("last")
    condition_rows = chart_conditions_for_timeframe(
        conditions, timeframe, close_features, live_features, closes, live_closes,
    )
    thresholds = []
    for condition in matching_conditions:
        if condition.get("valueType") != "number" or str(condition.get("indicator")) == "key_levels":
            continue
        threshold = number(condition.get("value"))
        if threshold is None:
            continue
        name = str(condition.get("indicator", "price"))
        pane = "indicator" if name in {"rsi", "bb_width"} else "price"
        thresholds.append({"value": threshold, "title": f"條件閾值 {format_value(threshold)}", "pane": pane})

    active_zones = []
    for zone in latest_zones:
        start_index = int(zone.get("originBar", zone.get("bornBar", -1)))
        if 0 <= start_index < len(candles):
            active_zones.append({
                **zone, "startTime": int(candles[start_index][0] // 1000),
                "originBar": start_index,
            })
    zone_side_tolerance = (
        (snapshots[-1].get("atr") or 0.0) * float(KEY_LEVEL_SETTINGS["breakBufferAtr"])
        if snapshots else 0.0
    )
    tick_size = number(market.get("tickSize")) or 0.0
    price_precision = 8
    if tick_size > 0:
        for digits in range(13):
            if round(tick_size, digits) == tick_size:
                price_precision = digits
                break
    return {
        "symbol": market["symbol"], "instId": market["id"], "timeframe": timeframe,
        "pricePrecision": price_precision,
        "mode": mode, "timeframes": sorted({condition_timeframe(item, timeframe) for item in conditions}),
        "candles": [
            {"time": int(row[0] // 1000), "open": row[1], "high": row[2],
             "low": row[3], "close": row[4], "volume": row[5]}
            for row in visible_bars
        ],
        "latestCloseTime": visible_times[-1] if visible_times else None,
        "liveTime": live_time, "livePrice": format_value(live_price),
        "closePrice": format_value(close_price), "livePriceTime": ticker.get("timestamp"),
        "indicators": indicator_plots, "thresholds": thresholds,
        "zones": active_zones, "zoneSideTolerance": zone_side_tolerance,
        "events": [marker for marker in event_markers
                   if marker["time"] >= (visible_times[0] if visible_times else 0)],
        "references": {**close_references, **live_references},
        "closeFeatures": {name: format_value(value) for name, value in key_level_features_close.items()},
        "liveFeatures": {name: format_value(value) for name, value in key_level_features_live.items()},
        "conditions": condition_rows,
        "updatedAt": int(time.time() * 1000),
    }


class Handler(BaseHTTPRequestHandler):
    server_version = "DeepcoinScreener/1.0"

    def log_message(self, format: str, *args: Any) -> None:
        return

    def send_json(self, status: int, payload: Any) -> None:
        body = json.dumps(payload, ensure_ascii=False).encode("utf-8")
        self.send_response(status)
        self.send_header("Content-Type", "application/json; charset=utf-8")
        self.send_header("Content-Length", str(len(body)))
        self.send_header("Cache-Control", "no-store")
        self.end_headers()
        self.wfile.write(body)

    def do_GET(self) -> None:
        parsed = urlparse(self.path)
        if parsed.path == "/api/displays":
            self.send_json(200, {"displays": carousel_displays()})
            return
        if parsed.path == "/api/market-audit":
            self.send_json(200, market_directory.health(details=True))
            return
        if parsed.path == "/trade":
            try:
                inst_id = (parse_qs(parsed.query).get("instId") or [""])[0]
                if market_directory.health().get("status") != "ready":
                    self.send_json(409, {"error": "官网合约清单正在重试核验，已暂停跳转，避免使用过期或错误入口。"})
                    return
                market = next((item for item in fetch_markets() if item["id"] == inst_id), None)
                if not market:
                    self.send_json(409, {"error": "该合约当前未通过官网交易清单核验，已阻止错误跳转。请刷新筛选结果并查看核验明细。"})
                    return
                self.send_response(302)
                self.send_header("Location", "https://www.deepcoin.com/swap/zh/" + urlquote(market["webSymbol"], safe=""))
                self.send_header("Cache-Control", "no-store")
                self.end_headers()
            except Exception as exc:
                self.send_json(503, {"error": str(exc)})
            return
        if parsed.path == "/api/health":
            directory = market_directory.health()
            self.send_json(200, {
                "ok": True,
                "exchangeConnected": directory.get("status") == "ready" and not directory.get("stale"),
                "marketDirectoryStatus": directory.get("status"),
                "marketDirectoryError": (directory.get("errors") or [None])[0],
                "exchange": "Deepcoin",
                "market": "linear swap",
                "version": APP_VERSION,
            })
            return
        if parsed.path == "/api/update/check":
            try:
                self.send_json(200, update_check(schedule=False))
            except Exception as exc:
                self.send_json(502, {"error": str(exc)})
            return
        if parsed.path == "/api/markets":
            try:
                quote = (parse_qs(parsed.query).get("quote") or ["USDT"])[0].upper()
                markets = [market for market in fetch_markets() if market["quote"] == quote]
                self.send_json(200, {"markets": markets, "count": len(markets), "quote": quote, "websiteCatalog": website_catalog_health(quote)})
            except Exception as exc:
                self.send_json(502, {"error": str(exc)})
            return
        if parsed.path == "/api/price-change":
            try:
                query = parse_qs(parsed.query)
                payload = {
                    "quote": (query.get("quote") or ["USDT"])[0],
                    "timeframe": (query.get("timeframe") or ["1h"])[0],
                    "maxSymbols": int((query.get("maxSymbols") or [100])[0]),
                    "symbols": (query.get("symbols") or [""])[0],
                    "direction": (query.get("direction") or ["desc"])[0],
                }
                cache_seconds = max(0, min(3600, int((query.get("cacheSeconds") or [0])[0])))
                self.send_json(200, finalize_market_response(cached_rank_price_changes(payload, cache_seconds), payload))
            except ValueError as exc:
                self.send_json(400, {"error": str(exc)})
            except Exception as exc:
                self.send_json(502, {"error": str(exc)})
            return
        asset = "index.html" if parsed.path in {"", "/", "/chart", "/carousel"} else parsed.path.lstrip("/")
        file_path = (STATIC_DIR / asset).resolve()
        if STATIC_DIR.resolve() not in file_path.parents or not file_path.is_file():
            self.send_error(404)
            return
        content_type = {".html": "text/html; charset=utf-8", ".css": "text/css; charset=utf-8", ".js": "text/javascript; charset=utf-8"}.get(file_path.suffix, "application/octet-stream")
        body = file_path.read_bytes()
        self.send_response(200)
        self.send_header("Content-Type", content_type)
        self.send_header("Content-Length", str(len(body)))
        self.send_header("Cache-Control", "no-store")
        self.end_headers()
        self.wfile.write(body)

    def do_POST(self) -> None:
        if urlparse(self.path).path == "/api/update/check":
            try:
                self.send_json(200, update_check(schedule=True))
            except Exception as exc:
                self.send_json(502, {"error": str(exc)})
            return
        if urlparse(self.path).path == "/api/carousel":
            try:
                length = int(self.headers.get("Content-Length", "0"))
                payload = json.loads(self.rfile.read(length) or b"{}")
                self.send_json(200, open_carousel_window(payload))
            except ValueError as exc:
                self.send_json(400, {"error": str(exc)})
            except Exception as exc:
                self.send_json(503, {"error": str(exc)})
            return
        if urlparse(self.path).path == "/api/market-audit":
            try:
                market_directory.get_markets(force=True)
                self.send_json(200, market_directory.health(details=True))
            except Exception:
                self.send_json(503, market_directory.health(details=True))
            return
        if urlparse(self.path).path not in {"/api/screen", "/api/price-change", "/api/chart"}:
            self.send_error(404)
            return
        try:
            length = int(self.headers.get("Content-Length", "0"))
            payload = json.loads(self.rfile.read(length) or b"{}")
            if urlparse(self.path).path == "/api/price-change":
                self.send_json(200, finalize_market_response(
                    cached_rank_price_changes(payload, int(number(payload.get("cacheSeconds")) or 0)), payload,
                ))
            elif urlparse(self.path).path == "/api/chart":
                self.send_json(200, chart_data(payload))
            else:
                self.send_json(200, finalize_market_response(screen(payload), payload))
        except ValueError as exc:
            self.send_json(400, {"error": str(exc)})
        except Exception as exc:
            self.send_json(502, {"error": str(exc)})


def main() -> None:
    port = int(__import__("os").environ.get("PORT", "8000"))
    server = ThreadingHTTPServer(("127.0.0.1", port), Handler)
    market_directory.start()
    print(f"Deepcoin 筛选器已启动：http://127.0.0.1:{port}")
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        pass
    finally:
        market_directory.stop()
        server.server_close()


if __name__ == "__main__":
    main()
