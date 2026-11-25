import unittest
from unittest.mock import patch

import app


def bar(index, close=100.0):
    return [index * 60_000, close, close + 1, close - 1, close, 1.0]


def market(symbol):
    return {
        "id": f"{symbol}-USDT-SWAP",
        "symbol": f"{symbol}USDT",
        "base": symbol,
        "quote": "USDT",
        "assetType": "linear",
        "webCatalogStatus": "available",
        "webListed": True,
    }


class ScreenDiagnosticsTests(unittest.TestCase):
    def test_diagnostics_separate_ticker_prefilter_and_close_live_results(self):
        active = market("ACTIVE")
        filtered = market("FILTERED")
        bars = [bar(index) for index in range(30)]
        conditions = [
            {"timeframe": "1m", "indicator": "change24h", "operator": ">", "value": 0},
            {
                "timeframe": "1m", "indicator": "price", "operator": ">",
                "valueType": "indicator", "valueIndicator": "ema", "valuePeriod": 2,
            },
        ]
        tickers = {
            active["id"]: {"last": 102.0, "change24h": 5.0, "volume24h": 100.0},
            filtered["id"]: {"last": 102.0, "change24h": -1.0, "volume24h": 50.0},
        }
        with patch.object(app, "fetch_markets", return_value=[active, filtered]), \
             patch.object(app, "fetch_tickers", return_value=tickers), \
             patch.object(app, "fetch_candles", return_value=bars):
            result = app.screen({"quote": "USDT", "timeframe": "1m", "maxSymbols": 2,
                                 "conditions": conditions})

        diagnostics = result["screeningDiagnostics"]
        self.assertEqual(diagnostics["selectedMarkets"], 2)
        self.assertEqual(diagnostics["tickerFiltered"], 1)
        self.assertEqual(diagnostics["candleCandidates"], 1)
        self.assertEqual(diagnostics["close"], {
            "matched": 0, "conditionsNotMet": 1, "missingData": 0,
        })
        self.assertEqual(diagnostics["live"], {
            "matched": 1, "conditionsNotMet": 0, "missingData": 0,
        })
        self.assertEqual(diagnostics["conditions"][0]["close"]["passed"], 1)
        self.assertEqual(diagnostics["conditions"][1]["close"]["notMet"], 1)
        self.assertEqual(diagnostics["conditions"][1]["live"]["passed"], 1)

    def test_diagnostics_classify_unavailable_indicator_history_as_missing(self):
        selected = market("SHORT")
        condition = {
            "timeframe": "1m", "indicator": "ema", "period": 20,
            "operator": ">", "valueType": "number", "value": 100,
        }
        with patch.object(app, "fetch_markets", return_value=[selected]), \
             patch.object(app, "fetch_tickers", return_value={selected["id"]: {
                 "last": 100.0, "change24h": 0.0, "volume24h": 100.0,
             }}), \
             patch.object(app, "fetch_candles", return_value=[]):
            result = app.screen({"quote": "USDT", "timeframe": "1m", "maxSymbols": 1,
                                 "conditions": [condition]})

        diagnostics = result["screeningDiagnostics"]
        self.assertEqual(diagnostics["close"]["missingData"], 1)
        self.assertEqual(diagnostics["live"]["missingData"], 1)
        self.assertEqual(diagnostics["conditions"][0]["close"]["missing"], 1)
        self.assertEqual(diagnostics["conditions"][0]["live"]["missing"], 1)

    def test_close_price_condition_does_not_use_live_price_when_candles_are_missing(self):
        selected = market("NO_CANDLES")
        condition = {
            "timeframe": "1m", "indicator": "price", "operator": ">",
            "valueType": "number", "value": 90,
        }
        with patch.object(app, "fetch_markets", return_value=[selected]), \
             patch.object(app, "fetch_tickers", return_value={selected["id"]: {
                 "last": 100.0, "change24h": 0.0, "volume24h": 100.0,
             }}), \
             patch.object(app, "fetch_candles", return_value=[]):
            result = app.screen({"quote": "USDT", "timeframe": "1m", "maxSymbols": 1,
                                 "conditions": [condition]})

        self.assertEqual(result["closeResults"], [])
        self.assertEqual(len(result["liveResults"]), 1)
        self.assertEqual(result["screeningDiagnostics"]["close"]["missingData"], 1)
        self.assertEqual(result["screeningDiagnostics"]["live"]["matched"], 1)

    def test_key_level_diagnostic_keeps_the_user_condition_as_one_entry(self):
        condition = {
            "timeframe": "1h", "indicator": "key_levels",
            "keyLevelSignal": "support_hammer_rejection", "keyLevelThreshold": 60,
            "keyLevelLookbackBars": 8,
        }
        with patch.object(app, "fetch_markets", return_value=[]), \
             patch.object(app, "fetch_tickers", return_value={}):
            result = app.screen({"quote": "USDT", "timeframe": "1h", "conditions": [condition]})

        diagnostics = result["screeningDiagnostics"]
        self.assertEqual(len(diagnostics["conditions"]), 1)
        self.assertIn("1h", diagnostics["conditions"][0]["label"])


if __name__ == "__main__":
    unittest.main()
