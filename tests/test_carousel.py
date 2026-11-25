import unittest
from threading import Lock
from unittest.mock import patch
from urllib.parse import parse_qs, urlparse

import app


class CarouselTests(unittest.TestCase):
    def test_rank_snapshot_cache_reuses_matching_results_and_returns_copies(self):
        payload = {
            "quote": "USDT", "timeframe": "1h", "direction": "desc",
            "maxSymbols": 25, "symbols": "BTCUSDT",
        }
        result = {
            "results": [{"instId": "BTC-USDT-SWAP"}],
            "errors": ["ETHUSDT candles unavailable"], "updatedAt": 123,
        }
        with patch.object(app, "_rank_result_cache", {}), \
             patch.object(app, "_rank_result_locks", {}), \
             patch.object(app, "_rank_result_guard", Lock()), \
             patch.object(app, "rank_price_changes", return_value=result) as scan:
            first = app.cached_rank_price_changes(payload)
            first["results"].clear()
            second = app.cached_rank_price_changes(payload, max_age_seconds=60)

        self.assertEqual(second["results"], [{"instId": "BTC-USDT-SWAP"}])
        self.assertEqual(second["errors"], ["ETHUSDT candles unavailable"])
        scan.assert_called_once_with(payload)

    def test_carousel_displays_returns_work_area_bounds(self):
        displays = app.carousel_displays()
        self.assertTrue(displays)
        self.assertEqual([item["index"] for item in displays], list(range(len(displays))))
        for display in displays:
            self.assertIn("name", display)
            self.assertIn("left", display)
            self.assertIn("width", display)
            self.assertIn("height", display)

    def test_carousel_rejects_invalid_interval_and_unknown_market(self):
        with patch.object(app, "fetch_markets", return_value=[]):
            with self.assertRaisesRegex(ValueError, "交易对数量"):
                app.open_carousel_window({"instIds": []})
            with self.assertRaisesRegex(ValueError, "官网核验目录"):
                app.open_carousel_window({"instIds": ["UNKNOWN-USDT-SWAP"], "interval": 1})

    def test_carousel_starts_browser_with_verified_ids_and_settings(self):
        market = {
            "id": "BTC-USDT-SWAP", "symbol": "BTCUSDT", "quote": "USDT",
            "webCatalogStatus": "available", "webListed": True,
        }
        display = {"index": 1, "name": "DISPLAY2", "left": 1920, "top": 0,
                   "width": 1600, "height": 900, "primary": False}
        process = type("Process", (), {"pid": 1234})()
        payload = {
            "instIds": [market["id"]], "quote": "USDT", "timeframe": "4h",
            "mode": "live", "interval": 2, "displayIndex": 1,
            "conditions": [],
            "chartIndicators": [{"name": "ema", "period": 20}, {"name": "bb", "period": 20, "deviations": 2}],
            "source": "ranking",
            "ranking": {
                "quote": "USDT", "timeframe": "1h", "direction": "asc",
                "maxSymbols": 25, "symbols": "", "refreshSeconds": 60,
                "firstRefreshSeconds": 43,
            },
        }
        class ImmediateThread:
            def __init__(self, target, args=(), **_kwargs):
                self.target, self.args = target, args

            def start(self):
                self.target(*self.args)

        with patch.object(app, "fetch_markets", return_value=[market]), \
             patch.object(app, "carousel_displays", return_value=[display]), \
             patch.object(app, "_carousel_browser", return_value="C:/Edge/msedge.exe"), \
             patch.object(app.subprocess, "Popen", return_value=process) as popen, \
             patch.object(app.threading, "Thread", ImmediateThread), \
             patch.object(app.tempfile, "gettempdir", return_value="C:/Temp"), \
             patch.object(app.Path, "mkdir"), \
             patch.object(app, "_position_carousel_window") as position:
            result = app.open_carousel_window(payload)

        self.assertTrue(result["ok"])
        self.assertEqual(result["pid"], 1234)
        self.assertEqual(result["display"], display)
        self.assertEqual(result["interval"], app.CAROUSEL_MIN_INTERVAL)
        self.assertEqual(result["source"], "ranking")
        self.assertEqual(result["rankRefreshSeconds"], 60)
        command = popen.call_args.args[0]
        self.assertEqual(command[:2], ["C:/Edge/msedge.exe", "--new-window"])
        self.assertTrue(any(value.startswith("--user-data-dir=") for value in command))
        self.assertIn("--no-first-run", command)
        self.assertIn("/carousel?", command[-1])
        self.assertIn("instIds=BTC-USDT-SWAP", command[-1])
        query = parse_qs(urlparse(command[-1]).query)
        self.assertEqual(query["source"], ["ranking"])
        self.assertEqual(query["rankTimeframe"], ["1h"])
        self.assertEqual(query["rankDirection"], ["asc"])
        self.assertEqual(query["rankRefreshSeconds"], ["60"])
        self.assertEqual(query["rankFirstRefreshSeconds"], ["43"])
        self.assertEqual(app.json.loads(query["chartIndicators"][0]), [
            {"name": "ema", "period": 20, "deviations": 2.0},
            {"name": "bb", "period": 20, "deviations": 2.0},
        ])
        position.assert_called_once_with(1234, display)

    def test_chart_indicator_settings_are_validated(self):
        self.assertEqual(app.validate_chart_indicators([{"name": "ema", "period": 20}]), [
            {"name": "ema", "period": 20, "deviations": 2.0},
        ])
        with self.assertRaisesRegex(ValueError, "period"):
            app.validate_chart_indicators([{"name": "ema", "period": 201}])


if __name__ == "__main__":
    unittest.main()
