from __future__ import annotations

import json
import hashlib
import hmac
import math
import secrets
import sys
import threading
import time
from concurrent.futures import ThreadPoolExecutor, as_completed
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from typing import Any
from urllib.parse import parse_qs, quote as urlquote, urlencode, urlparse
from urllib.request import Request, urlopen

from market_directory import MarketDirectory, MarketDirectoryError


# PyInstaller extracts bundled static files to _MEIPASS at runtime.
ROOT = Path(getattr(sys, "_MEIPASS", Path(__file__).resolve().parent))
STATIC_DIR = ROOT / "static"
API_BASE = "https://api.deepcoin.com"
WEB_API_BASE = "https://net-wapi.deepcoin.com"
WEB_API_SIGN_SECRET = "61dd6c49529a05569900e71f49a0cd87"
TIMEFRAMES = {"1m": 60_000, "5m": 300_000, "15m": 900_000, "30m": 1_800_000, "1h": 3_600_000, "4h": 14_400_000, "12h": 43_200_000, "1d": 86_400_000, "1w": 604_800_000}
DEEPCOIN_BARS = {"1m": "1m", "5m": "5m", "15m": "15m", "30m": "30m", "1h": "1H", "4h": "4H", "12h": "12H", "1d": "1D", "1w": "1W"}
DEFAULT_INDICATOR_PERIOD = 20
MAX_CANDLES = 240
HTTP_TIMEOUT = 15
MAX_WORKERS = 10
_candle_cache: dict[tuple[str, str], tuple[float, list[list[float]]]] = {}
_candle_cache_lock = threading.Lock()


class DeepcoinError(RuntimeError):
    pass


def deepcoin_get(path: str, params: dict[str, Any] | None = None) -> Any:
    query = "?" + urlencode({key: value for key, value in (params or {}).items() if value is not None}) if params else ""
    request = Request(
        API_BASE + path + query,
        headers={"Accept": "application/json", "User-Agent": "deepcoin-screen/1.0"},
    )
    last_error: Exception | None = None
    for attempt in range(5):
        try:
            with urlopen(request, timeout=HTTP_TIMEOUT) as response:
                payload = json.loads(response.read().decode("utf-8"))
            break
        except Exception as exc:
            last_error = exc
            if attempt < 4:
                time.sleep(0.3 * (attempt + 1))
    else:
        detail = repr(last_error) if last_error else "未知错误"
        raise DeepcoinError(f"Deepcoin 接口请求失败：{detail}") from last_error
    if str(payload.get("code")) != "0":
        raise DeepcoinError(payload.get("msg") or "Deepcoin 返回了错误")
    return payload.get("data")


def website_catalog_request(system: str | None = None) -> dict[str, Any]:
    """Read anonymous website metadata; no account credentials are involved."""
    alphabet = "ABCDEFGHJKMNPQRSTWXYZabcdefhijkmnprstwxyz2345678"
    # The public website signs its anonymous catalogue request with sorted
    # scalar parameters. Keep this isolated so a website API change is easy to
    # diagnose without affecting market data requests.
    last_error: Exception | None = None
    for attempt in range(3):
        try:
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
            with urlopen(request, timeout=HTTP_TIMEOUT) as response:
                payload = json.loads(response.read().decode("utf-8"))
            if not isinstance(payload, dict) or str(payload.get("retCode")) != "0" or not isinstance(payload.get("retData"), dict):
                raise DeepcoinError("官网合约清单结构或返回码异常")
            break
        except Exception as exc:
            last_error = exc
            if attempt < 2:
                time.sleep(0.35 * (attempt + 1))
    else:
        raise DeepcoinError(f"官网合约清单请求失败：{system}：{last_error!r}") from last_error
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


def fetch_candles(inst_id: str, timeframe: str, required_period: int = DEFAULT_INDICATOR_PERIOD, limit_override: int | None = None) -> list[list[float]]:
    cache_key = (inst_id, timeframe)
    # Indicators only need a warm-up window beyond their longest period; downloading 240
    # candles for every symbol makes a 100-symbol scan unnecessarily expensive.
    required_limit = limit_override if limit_override is not None else max(70, min(MAX_CANDLES, required_period + 30))
    required_limit = max(2, min(MAX_CANDLES, required_limit))
    duration = TIMEFRAMES[timeframe]
    with _candle_cache_lock:
        cached = _candle_cache.get(cache_key)
        if cached and len(cached[1]) >= required_period:
            now_ms = int(time.time() * 1000)
            expected_last_close = (now_ms // duration) * duration - duration
            last_cached_candle = cached[1][-1][0] if cached[1] else 0
            candle_is_current = last_cached_candle >= expected_last_close
            recent_fetch = time.time() - cached[0] < 10
            if candle_is_current or recent_fetch:
                return list(cached[1])
    rows = deepcoin_get(
        "/deepcoin/market/candles",
        {"instId": inst_id, "bar": DEEPCOIN_BARS[timeframe], "limit": required_limit},
    ) or []
    candles = []
    for row in rows:
        if len(row) < 6:
            continue
        parsed = [number(value) for value in row[:6]]
        if all(value is not None for value in parsed):
            candles.append(parsed)  # timestamp, open, high, low, close, volume
    candles.sort(key=lambda row: row[0])
    now_ms = int(time.time() * 1000)
    if candles and candles[-1][0] + duration > now_ms:
        candles.pop()
    with _candle_cache_lock:
        _candle_cache[cache_key] = (time.time(), candles)
    return candles


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


def make_metrics(market: dict[str, Any], ticker: dict[str, Any], candles: list[list[float]], live: bool = False) -> dict[str, Any]:
    closes = [row[4] for row in candles]
    calculation_closes = list(closes)
    if live and ticker.get("last") is not None:
        calculation_closes.append(ticker["last"])
    display_price = ticker.get("last") if live else (closes[-1] if closes else ticker.get("last"))
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
    }


def resolve_indicator(condition: dict[str, Any], metrics: dict[str, Any], closes: list[float]) -> float | None:
    indicator = str(condition.get("indicator", "price"))
    if indicator in {"change24h", "volume24h"}:
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
    else:
        right = number(condition.get("value"))
    return compare(left, str(condition.get("operator", ">")), right)


def condition_timeframe(condition: dict[str, Any], default: str) -> str:
    timeframe = str(condition.get("timeframe") or default)
    if timeframe not in TIMEFRAMES:
        raise ValueError(f"不支持的条件周期：{timeframe}")
    return timeframe


def condition_period(condition: dict[str, Any]) -> int:
    left = int(number(condition.get("period")) or DEFAULT_INDICATOR_PERIOD)
    right = int(number(condition.get("valuePeriod")) or DEFAULT_INDICATOR_PERIOD)
    return max(1, min(200, max(left, right)))


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
        if condition.get("valueType") != "indicator":
            continue
        left_indicator = str(condition.get("indicator", "price"))
        right_indicator = str(condition.get("valueIndicator", "price"))
        left_period = int(number(condition.get("period")) or DEFAULT_INDICATOR_PERIOD)
        right_period = int(number(condition.get("valuePeriod")) or DEFAULT_INDICATOR_PERIOD)
        same_series = left_indicator == right_indicator and left_period == right_period
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


def screen(payload: dict[str, Any]) -> dict[str, Any]:
    started_at = time.perf_counter()
    display_timeframe = str(payload.get("timeframe", "1h"))
    if display_timeframe not in TIMEFRAMES:
        raise ValueError("不支持的 K 线周期")
    quote = str(payload.get("quote", "USDT")).upper()
    conditions = payload.get("conditions") or []
    if not isinstance(conditions, list) or len(conditions) > 12:
        raise ValueError("筛选条件最多 12 条")
    validate_conditions(conditions)
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
    if not markets:
        return {"closeResults": [], "liveResults": [], "results": [], "scanned": 0, "totalMarkets": len(all_markets), "timeframe": display_timeframe, "timeframes": [], "updatedAt": int(time.time() * 1000)}

    # Simple 24h filters can reject symbols without downloading any candles.
    candidate_markets = [market for market in markets if passes_ticker_filters(market, tickers.get(market["id"], {}), conditions)]

    # Each (symbol, timeframe) is fetched once. The same closed candles drive both result sets.
    condition_timeframes = {condition_timeframe(condition, display_timeframe) for condition in conditions}
    # Pure ticker conditions (24h change / volume) do not need a K-line request.
    required_timeframes = {
        condition_timeframe(condition, display_timeframe)
        for condition in conditions
        if needs_candles(condition)
    }
    periods_by_timeframe = {
        timeframe: max([condition_period(condition) for condition in conditions if condition_timeframe(condition, display_timeframe) == timeframe] or [DEFAULT_INDICATOR_PERIOD])
        for timeframe in required_timeframes
    }
    candle_sets: dict[tuple[str, str], list[list[float]]] = {}
    failed_candles = 0
    candle_jobs = 0
    candle_errors: list[str] = []

    def load_candles(market: dict[str, Any], timeframe: str) -> tuple[tuple[str, str], list[list[float]] | None, str | None]:
        try:
            return (market["id"], timeframe), fetch_candles(market["id"], timeframe, periods_by_timeframe[timeframe]), None
        except DeepcoinError as exc:
            return (market["id"], timeframe), None, f"{market['symbol']} {timeframe}: {exc}"

    jobs = [(market, timeframe) for market in candidate_markets for timeframe in required_timeframes]
    candle_jobs = len(jobs)
    with ThreadPoolExecutor(max_workers=min(MAX_WORKERS, max(1, len(jobs)))) as executor:
        futures = [executor.submit(load_candles, market, timeframe) for market, timeframe in jobs]
        for future in as_completed(futures):
            key, candles, error = future.result()
            if candles is None:
                failed_candles += 1
                if error and len(candle_errors) < 3:
                    candle_errors.append(error)
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
        for timeframe in required_timeframes:
            candles = candle_sets.get((market["id"], timeframe), [])
            closes = [row[4] for row in candles]
            closes_by_tf[timeframe] = closes
            live_closes_by_tf[timeframe] = closes + ([ticker["last"]] if ticker.get("last") is not None else [])
            close_metrics_by_tf[timeframe] = make_metrics(market, ticker, candles, live=False)
            live_metrics_by_tf[timeframe] = make_metrics(market, ticker, candles, live=True)
        def metrics_for(timeframe: str, live: bool) -> tuple[dict[str, Any], list[float]]:
            if timeframe in (live_metrics_by_tf if live else close_metrics_by_tf):
                return (live_metrics_by_tf if live else close_metrics_by_tf)[timeframe], (live_closes_by_tf if live else closes_by_tf)[timeframe]
            return make_metrics(market, ticker, [], live=live), []

        close_ok = all(
            evaluate_condition(condition, *metrics_for(condition_timeframe(condition, display_timeframe), False))
            for condition in conditions
        )
        live_ok = all(
            evaluate_condition(condition, *metrics_for(condition_timeframe(condition, display_timeframe), True))
            for condition in conditions
        )
        display_close = close_metrics_by_tf.get(display_timeframe) or make_metrics(market, ticker, [], live=False)
        display_live = live_metrics_by_tf.get(display_timeframe) or make_metrics(market, ticker, [], live=True)
        common = dict(display_close)
        common["livePrice"] = display_live.get("livePrice")
        common["closePrice"] = display_close.get("closePrice")
        common["timeframes"] = sorted(condition_timeframes)
        common["candles"] = sum(len(candle_sets.get((market["id"], timeframe), [])) for timeframe in required_timeframes)
        if close_ok:
            close_result = dict(common)
            close_result["price"] = display_close.get("closePrice") or display_close.get("livePrice")
            close_result["mode"] = "close"
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
            self.send_json(200, {"ok": True, "exchange": "Deepcoin", "market": "linear swap"})
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
                self.send_json(200, finalize_market_response(rank_price_changes(payload), payload))
            except ValueError as exc:
                self.send_json(400, {"error": str(exc)})
            except Exception as exc:
                self.send_json(502, {"error": str(exc)})
            return
        asset = "index.html" if parsed.path in {"", "/"} else parsed.path.lstrip("/")
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
        if urlparse(self.path).path == "/api/market-audit":
            try:
                market_directory.get_markets(force=True)
                self.send_json(200, market_directory.health(details=True))
            except Exception:
                self.send_json(503, market_directory.health(details=True))
            return
        if urlparse(self.path).path not in {"/api/screen", "/api/price-change"}:
            self.send_error(404)
            return
        try:
            length = int(self.headers.get("Content-Length", "0"))
            payload = json.loads(self.rfile.read(length) or b"{}")
            if urlparse(self.path).path == "/api/price-change":
                self.send_json(200, finalize_market_response(rank_price_changes(payload), payload))
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
