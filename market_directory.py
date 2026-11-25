"""Continuously reconcile public instruments with the website's two catalogues."""
from __future__ import annotations

from concurrent.futures import ThreadPoolExecutor
from copy import deepcopy
import threading
import time
from typing import Any, Callable
from urllib.parse import quote as urlquote


class MarketDirectoryError(RuntimeError):
    pass


class MarketDirectory:
    def __init__(self, instruments: Callable, groups: Callable, routes: Callable,
                 refresh_seconds: float = 60, retry_seconds: float = 15):
        self.instruments = instruments
        self.groups = groups
        self.routes = routes
        self.refresh_seconds = refresh_seconds
        self.retry_seconds = retry_seconds
        self._lock = threading.Lock()
        self._refresh_lock = threading.Lock()
        self._markets: list[dict[str, Any]] | None = None
        self._checked = 0.0
        self._attempted = 0.0
        self._stop = threading.Event()
        self._thread: threading.Thread | None = None
        self._health: dict[str, Any] = {
            "status": "starting", "checkedAt": None, "attemptAt": None,
            "refreshing": False, "errors": [], "entries": [], "changes": [],
            "refreshSeconds": refresh_seconds, "retrySeconds": retry_seconds,
        }

    def health(self, details: bool = False) -> dict[str, Any]:
        with self._lock:
            result = deepcopy(self._health)
            result["stale"] = self._health["status"] == "error" or self._markets is None or time.monotonic() - self._checked >= self.refresh_seconds
            result["ageSeconds"] = round(time.monotonic() - self._checked, 1) if self._markets is not None else None
            result["verifiedSymbols"] = [item["symbol"] for item in (self._markets or [])]
        if not details:
            result.pop("entries", None)
            result.pop("changes", None)
        return result

    def get_markets(self, force: bool = False) -> list[dict[str, Any]]:
        request_started = time.monotonic()
        if not force:
            with self._lock:
                if self._health["status"] == "ready" and self._markets is not None and request_started - self._checked < self.refresh_seconds:
                    return deepcopy(self._markets)
                if self._health["status"] == "error":
                    if self._markets is not None:
                        return deepcopy(self._markets)
                    detail = "; ".join(self._health.get("errors") or [])
                    raise MarketDirectoryError(f"官网合约清单核验失败：{detail}。后台会自动重试，筛选仅使用已确认合约。")
                if self._health["refreshing"] or self._health["status"] == "starting":
                    if self._markets is not None:
                        return deepcopy(self._markets)
                    raise MarketDirectoryError("官网合约清单正在核验，请稍后重试；核验完成后会自动恢复筛选。")
                raise MarketDirectoryError("官网合约清单已过期，正在等待后台核验；核验完成后会自动恢复筛选。")
        self._refresh_lock.acquire()
        try:
            # Concurrent requests share the same completed refresh.
            with self._lock:
                now = time.monotonic()
                if self._health["status"] == "ready" and self._markets is not None and now - self._checked < self.refresh_seconds and self._checked >= request_started:
                    return deepcopy(self._markets)
                self._attempted = now
                self._health.update(refreshing=True, attemptAt=int(time.time() * 1000))
            try:
                markets, audit = self._reconcile()
            except Exception as exc:
                with self._lock:
                    self._health.update(status="error", refreshing=False, errors=[str(exc)])
                raise MarketDirectoryError(f"官网合约清单核验失败：{exc}。已保留上次核验记录并自动重试，请勿把本次故障视为下架。") from exc
            with self._lock:
                previous = {item["symbol"] for item in (self._markets or [])}
                current = {item["symbol"] for item in markets}
                checked_at = int(time.time() * 1000)
                changes = self._health["changes"]
                added, removed = sorted(current - previous), sorted(previous - current)
                if self._markets is None or added or removed:
                    changes = (changes + [{"at": checked_at, "initial": self._markets is None, "added": added, "removed": removed}])[-20:]
                self._markets = markets
                self._checked = time.monotonic()
                self._health.update(audit, status="ready", checkedAt=checked_at, refreshing=False, errors=[], changes=changes)
                return deepcopy(markets)
        finally:
            self._refresh_lock.release()

    def _reconcile(self) -> tuple[list[dict[str, Any]], dict[str, Any]]:
        raw = self.instruments()
        if not isinstance(raw, list) or not raw or any(not isinstance(item, dict) for item in raw):
            raise MarketDirectoryError("公开合约接口返回空数据或结构异常")
        scope = [item for item in raw if item.get("ctType") == "linear" and item.get("quoteCcy") in {"USDT", "USDC"}]
        if not scope:
            raise MarketDirectoryError("公开合约接口没有返回线性 USDT/USDC 合约，需检查接口结构")
        for item in scope:
            if not item.get("instId") or not item.get("symbol") or not item.get("state"):
                raise MarketDirectoryError("公开合约字段缺失，暂停发布本次核验结果")
        systems = {str(item.get("productGroup") or ("SwapC" if item["quoteCcy"] == "USDC" else "SwapU")) for item in scope}
        # Absence is only confirmed when both website sources agree. Neither
        # a timeout nor a malformed response can become an empty blacklist.
        with ThreadPoolExecutor(max_workers=min(6, len(systems) + 1)) as executor:
            route_future = executor.submit(self.routes, systems)
            group_futures = {system: executor.submit(self.groups, system) for system in systems}
            route_sets = route_future.result()
            group_sets = {system: future.result() for system, future in group_futures.items()}
        if not isinstance(route_sets, dict) or not any(route_sets.values()) or not any(group_sets.values()):
            raise MarketDirectoryError("官网合约清单异常为空，不能据此判断批量下架")
        if any(system not in route_sets for system in systems):
            raise MarketDirectoryError("官网路由清单缺少产品组，需等待接口恢复或同步")
        for system in systems:
            if not route_sets[system] and not group_sets[system]:
                raise MarketDirectoryError(f"官网 {system} 两份清单均异常为空，不能据此判断整个产品组下架")
        entries, markets = [], []
        public_routes: set[tuple[str, str]] = set()
        counts = {quote: {"public": 0, "available": 0, "excluded": 0, "pending": 0} for quote in ("USDT", "USDC")}
        for item in scope:
            symbol = str(item["symbol"]).strip().upper()
            inst_id = str(item["instId"])
            group = str(item.get("productGroup") or ("SwapC" if item["quoteCcy"] == "USDC" else "SwapU"))
            canonical = inst_id.upper().removesuffix("-SWAP").replace("-", "")
            candidates = {symbol, canonical}
            route_matches = candidates & route_sets[group]
            group_matches = candidates & group_sets[group]
            public_routes.update((group, value) for value in candidates)
            quote = item["quoteCcy"]
            if item["state"] != "live":
                entries.append({"symbol": symbol, "quote": quote, "status": "inactive", "reason": f"行情接口状态为 {item['state']}，当前非交易中"})
                continue
            counts[quote]["public"] += 1
            common = route_matches & group_matches
            if common:
                web_symbol = symbol if symbol in common else sorted(common)[0]
                markets.append({
                    "id": inst_id, "symbol": symbol, "base": item.get("baseCcy"), "quote": quote,
                    "productGroup": group, "assetType": str(item.get("assetType") or "").lower(),
                    "active": True, "maxLeverage": item.get("lever"), "tickSize": item.get("tickSz"), "webSymbol": web_symbol,
                    "webCatalogStatus": "available", "webListed": True,
                    "tradeUrl": "https://www.deepcoin.com/swap/zh/" + urlquote(web_symbol, safe=""),
                    "webTradeStatus": "available", "webTradeReason": "",
                })
                counts[quote]["available"] += 1
            elif route_matches or group_matches:
                counts[quote]["pending"] += 1
                entries.append({"symbol": symbol, "quote": quote, "status": "pending", "reason": "官网两份清单暂不一致，待核验；未判定为下架，将自动重试"})
            else:
                counts[quote]["excluded"] += 1
                entries.append({"symbol": symbol, "quote": quote, "status": "not_listed", "reason": "行情存在，但官网路由及交易清单均未列出；每轮重新核验，可自动恢复"})
        # Website-first listings are visible in diagnostics instead of being
        # silently discarded while the public instruments API catches up.
        for group in sorted(systems):
            for symbol in sorted(route_sets[group] | group_sets[group]):
                if (group, symbol) not in public_routes:
                    quote = "USDC" if symbol.endswith("USDC") else "USDT" if symbol.endswith("USDT") else ""
                    if quote:
                        counts[quote]["pending"] += 1
                        entries.append({"symbol": symbol, "quote": quote, "status": "pending", "reason": "官网已列出，公开合约接口尚未同步，待核验并自动重试"})
        markets.sort(key=lambda item: item["symbol"])
        return markets, {
            "publicMarkets": sum(value["public"] for value in counts.values()),
            "availableMarkets": len(markets),
            "excludedMarkets": sum(value["excluded"] for value in counts.values()),
            "pendingMarkets": sum(value["pending"] for value in counts.values()),
            "quotes": counts, "entries": entries,
            "systems": {system: {"routeCount": len(route_sets[system]), "tradeCount": len(group_sets[system])} for system in systems},
        }

    def start(self) -> None:
        if self._thread and self._thread.is_alive():
            return
        def monitor() -> None:
            while not self._stop.is_set():
                try:
                    self.get_markets(force=True)
                except MarketDirectoryError:
                    pass
                delay = self.retry_seconds if self.health().get("status") == "error" or self.health().get("pendingMarkets") else self.refresh_seconds
                self._stop.wait(delay)
        self._thread = threading.Thread(target=monitor, name="deepcoin-catalog-audit", daemon=True)
        self._thread.start()

    def stop(self) -> None:
        self._stop.set()
