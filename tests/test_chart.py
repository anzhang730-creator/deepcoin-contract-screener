import unittest
from unittest.mock import patch

import app


def bar(timestamp, open_price, high, low, close, volume=1.0):
    return [timestamp, open_price, high, low, close, volume]


def test_market():
    return {
        "id": "TEST-USDT-SWAP", "symbol": "TESTUSDT", "base": "TEST", "quote": "USDT",
        "assetType": "linear", "tickSize": "0.1", "webCatalogStatus": "available", "webListed": True,
    }


class ChartDataTests(unittest.TestCase):
    def test_chart_can_plot_indicators_without_screening_conditions(self):
        candles = [
            bar(index * app.TIMEFRAMES["1h"], 100 + index, 101 + index, 99 + index, 100 + index)
            for index in range(80)
        ]
        market = test_market()
        with patch.object(app, "fetch_markets", return_value=[market]), \
             patch.object(app, "fetch_candles", return_value=candles), \
             patch.object(app, "fetch_tickers", return_value={market["id"]: {"last": 180.0}}):
            result = app.chart_data({
                "instId": market["id"], "timeframe": "1h", "conditions": [],
                "chartIndicators": [
                    {"name": "ema", "period": 12},
                    {"name": "ma", "period": 20},
                    {"name": "bb", "period": 20, "deviations": 2},
                ],
            })

        plots = {(plot["name"], plot["period"]) for plot in result["indicators"]}
        self.assertIn(("ema", 12), plots)
        self.assertIn(("ma", 20), plots)
        self.assertTrue({("bb_upper", 20), ("bb_middle", 20), ("bb_lower", 20)}.issubset(plots))

    def test_unconditioned_chart_keeps_default_history_instead_of_recent_scan_window(self):
        candles = [
            bar(index * app.TIMEFRAMES["1h"], 100, 101, 99, 100 + index * 0.01)
            for index in range(600)
        ]
        market = test_market()
        with patch.object(app, "fetch_markets", return_value=[market]), \
             patch.object(app, "fetch_candles", return_value=candles) as fetch_candles, \
             patch.object(app, "fetch_tickers", return_value={market["id"]: {"last": 105.0}}):
            result = app.chart_data({
                "instId": market["id"], "timeframe": "1h", "mode": "live", "conditions": [],
            })

        self.assertEqual(fetch_candles.call_args.args[2:], (app.MIN_INDICATOR_HISTORY - 30, app.MIN_INDICATOR_HISTORY))
        self.assertEqual(len(result["candles"]), app.MIN_INDICATOR_HISTORY)
        self.assertEqual(result["candles"][0]["time"], candles[-app.MIN_INDICATOR_HISTORY][0] // 1000)
        self.assertEqual(len({item["time"] for item in result["candles"]}), app.MIN_INDICATOR_HISTORY)

    def test_period_200_chart_requests_same_warmup_window_as_screening(self):
        candles = [
            bar(index * app.TIMEFRAMES["1h"], 100 + index * 0.1, 101 + index * 0.1,
                99 + index * 0.1, 100 + index * 0.1 + (index % 7) * 0.01)
            for index in range(2000)
        ]
        condition = {
            "timeframe": "1h", "indicator": "ema", "period": 200, "operator": ">",
            "valueType": "number", "value": 100,
        }
        with patch.object(app, "fetch_markets", return_value=[test_market()]), \
             patch.object(app, "fetch_candles", return_value=candles) as fetch_candles, \
             patch.object(app, "fetch_tickers", return_value={test_market()["id"]: {"last": 310.0}}):
            result = app.chart_data({
                "instId": test_market()["id"], "timeframe": "1h", "conditions": [condition],
            })

        self.assertEqual(fetch_candles.call_args.args[2:], (1970, 2000))
        self.assertTrue(result["conditions"][0]["closePass"])

    def test_chart_reports_price_distance_to_selected_indicator_line(self):
        candles = [bar(index * app.TIMEFRAMES["1h"], 100, 101, 99, 100) for index in range(50)]
        condition = {
            "timeframe": "1h", "indicator": "price", "operator": "within_percent",
            "valueType": "indicator", "valueIndicator": "ema", "valuePeriod": 20, "value": 0.5,
        }
        with patch.object(app, "fetch_markets", return_value=[test_market()]), \
             patch.object(app, "fetch_candles", return_value=candles), \
             patch.object(app, "fetch_tickers", return_value={test_market()["id"]: {"last": 102.0}}):
            result = app.chart_data({
                "instId": test_market()["id"], "timeframe": "1h", "conditions": [condition],
            })

        self.assertTrue(result["conditions"][0]["closePass"])
        self.assertFalse(result["conditions"][0]["livePass"])
        self.assertIn("EMA20", {plot["title"] for plot in result["indicators"]})

    def test_plotted_indicator_values_match_screening_calculations(self):
        candles = [
            bar(index * app.TIMEFRAMES["1h"], 100 + index, 102 + index, 99 + index, 101 + index)
            for index in range(80)
        ]
        condition = {
            "timeframe": "1h", "indicator": "ema", "period": 20, "operator": ">",
            "valueType": "indicator", "valueIndicator": "ema", "valuePeriod": 60,
        }
        rsi_condition = {
            "timeframe": "1h", "indicator": "rsi", "period": 14, "operator": ">",
            "valueType": "number", "value": 0,
        }
        band_condition = {
            "timeframe": "1h", "indicator": "bb_upper", "period": 20, "operator": ">",
            "valueType": "number", "value": 0,
        }
        with patch.object(app, "fetch_markets", return_value=[test_market()]), \
             patch.object(app, "fetch_candles", return_value=candles), \
             patch.object(app, "fetch_tickers", return_value={test_market()["id"]: {"last": 181.0}}):
            result = app.chart_data({
                "instId": test_market()["id"], "timeframe": "1h",
                "conditions": [condition, rsi_condition, band_condition],
            })

        plots = {plot["title"]: plot for plot in result["indicators"]}
        close_values = [row[4] for row in candles]
        self.assertAlmostEqual(plots["EMA20"]["closed"][-1]["value"], app.ema(close_values, 20))
        self.assertAlmostEqual(plots["EMA60"]["closed"][-1]["value"], app.ema(close_values, 60))
        self.assertAlmostEqual(plots["RSI14"]["closed"][-1]["value"], app.rsi(close_values, 14))
        self.assertAlmostEqual(plots["BB20 上轨"]["closed"][-1]["value"], app.bollinger(close_values, 20, 2)[0])
        self.assertTrue(result["conditions"][0]["closePass"])
        self.assertTrue(result["conditions"][0]["livePass"])
        self.assertEqual(result["pricePrecision"], 1)

    def test_key_level_reference_condition_is_reported_and_plotted(self):
        day = 86_400_000
        candles = [bar(day + index * app.TIMEFRAMES["1h"], 12, 14, 11, 13) for index in range(30)]
        daily = [bar(0, 9, 12, 8, 11), bar(day, 11, 15, 10, 14)]
        weekly = [bar(-7 * day, 10, 20, 5, 15), bar(0, 15, 25, 7, 18)]
        condition = {
            "timeframe": "1h", "indicator": "key_levels",
            "keyLevelSignal": "price_above_previous_day_high",
        }

        def recent(_inst_id, timeframe, _limit):
            return {"1h": candles[-3:], "1d": daily, "1w": weekly}[timeframe]

        with patch.object(app, "fetch_markets", return_value=[test_market()]), \
             patch.object(app, "fetch_candles", return_value=candles), \
             patch.object(app, "fetch_recent_candles", side_effect=recent), \
             patch.object(app, "fetch_tickers", return_value={test_market()["id"]: {"last": 14.0}}):
            result = app.chart_data({
                "instId": test_market()["id"], "timeframe": "1h", "conditions": [condition],
            })

        self.assertEqual(result["closeFeatures"]["kl_previous_day_high"], 12)
        self.assertEqual(result["liveFeatures"]["kl_previous_day_high"], 12)
        self.assertTrue(result["conditions"][0]["closePass"])
        self.assertTrue(result["conditions"][0]["livePass"])
        self.assertIn("kl_previous_day_high", result["references"])
        self.assertEqual(result["thresholds"], [])

    def test_chart_zones_start_at_their_source_candle_and_exclude_expired_zones(self):
        candles = [
            bar(index * app.TIMEFRAMES["1h"], 100, 102, 98, 100)
            for index in range(620)
        ]
        expired = {
            "top": 99.0, "bottom": 98.0, "kind": 1, "score": 70.0, "touches": 1,
            "bornBar": 3, "confluence": 1, "containsHtf": False,
            "awaitingRetest": False, "pendingReaction": False,
        }
        active = {
            "top": 101.0, "bottom": 100.0, "kind": -1, "score": 88.0, "touches": 2,
            "bornBar": 12, "originBar": 7, "originPrice": 103.0,
            "confluence": 2, "containsHtf": True,
            "awaitingRetest": False, "pendingReaction": False,
        }

        class FakeCalculator:
            def __init__(self, **_kwargs):
                self.index = 0

            def process(self, _bars, _htf_events=None):
                self.index += 1
                return {
                    "zones": [expired] if self.index < 12 else [active],
                    "events": {}, "atr": 1.0, "price": 100.0,
                }

        condition = {
            "timeframe": "1h", "indicator": "key_levels",
            "keyLevelSignal": "support_exists",
        }
        with patch.object(app, "fetch_markets", return_value=[test_market()]), \
             patch.object(app, "fetch_candles", return_value=candles), \
             patch.object(app, "fetch_tickers", return_value={test_market()["id"]: {"last": 100.0}}), \
             patch.object(app, "confirmed_htf_events", return_value={}), \
             patch.object(app, "KeyLevelCalculator", FakeCalculator):
            result = app.chart_data({
                "instId": test_market()["id"], "timeframe": "1h", "conditions": [condition],
            })

        self.assertEqual(len(result["zones"]), 1)
        self.assertEqual(result["zones"][0]["bornBar"], 12)
        self.assertEqual(result["zones"][0]["originBar"], 7)
        self.assertEqual(result["zones"][0]["originPrice"], 103.0)
        self.assertEqual(result["zones"][0]["startTime"], candles[7][0] // 1000)
        self.assertEqual(result["candles"][0]["time"], candles[7][0] // 1000)
        self.assertNotIn("zoneSeries", result)

    def test_support_hammer_chart_marks_closed_event_without_reusing_it_live(self):
        candles = [
            bar(index * app.TIMEFRAMES["1h"], 10, 11, 9, 10)
            for index in range(40)
        ]
        support = {
            "top": 9.5, "bottom": 8.5, "kind": 1, "score": 80.0, "touches": 1,
            "bornBar": 0, "originBar": 0, "originPrice": 9.0, "confluence": 1,
            "containsHtf": False, "awaitingRetest": False, "pendingReaction": False,
        }
        resistance = {
            "top": 11.5, "bottom": 10.5, "kind": -1, "score": 80.0, "touches": 1,
            "bornBar": 0, "originBar": 0, "originPrice": 11.0, "confluence": 1,
            "containsHtf": False, "awaitingRetest": False, "pendingReaction": False,
        }

        class FakeCalculator:
            def __init__(self, **_kwargs):
                self.index = 0

            def process(self, _bars, _htf_events=None):
                is_latest = self.index == len(candles) - 1
                snapshot = {
                    "zones": [support, resistance],
                    "support": support, "resistance": resistance,
                    "events": {
                        "supportHammer": is_latest,
                        "resistanceInvertedHammer": is_latest,
                    },
                    "atr": 1.0, "price": candles[self.index][4],
                }
                self.index += 1
                return snapshot

        condition = {
            "timeframe": "1h", "indicator": "key_levels",
            "keyLevelSignal": "support_hammer_rejection", "keyLevelThreshold": 75,
            "keyLevelLookbackBars": 6,
        }
        inverted_condition = {
            "timeframe": "1h", "indicator": "key_levels",
            "keyLevelSignal": "resistance_inverted_hammer_rejection", "keyLevelThreshold": 75,
            "keyLevelLookbackBars": 6,
        }
        with patch.object(app, "fetch_markets", return_value=[test_market()]), \
             patch.object(app, "fetch_candles", return_value=candles), \
             patch.object(app, "fetch_tickers", return_value={test_market()["id"]: {"last": 10.5}}), \
             patch.object(app, "confirmed_htf_events", return_value={}), \
             patch.object(app, "KeyLevelCalculator", FakeCalculator):
            result = app.chart_data({
                "instId": test_market()["id"], "timeframe": "1h",
                "conditions": [condition, inverted_condition],
            })

        self.assertEqual(result["closeFeatures"]["kl_event_support_hammer"], 1.0)
        self.assertEqual(result["liveFeatures"]["kl_event_support_hammer"], 0.0)
        self.assertEqual(result["closeFeatures"]["kl_event_resistance_inverted_hammer"], 1.0)
        self.assertEqual(result["liveFeatures"]["kl_event_resistance_inverted_hammer"], 0.0)
        self.assertTrue(result["conditions"][0]["closePass"])
        self.assertFalse(result["conditions"][0]["livePass"])
        self.assertTrue(result["conditions"][1]["closePass"])
        self.assertFalse(result["conditions"][1]["livePass"])
        self.assertTrue(any(marker.get("text") == "锤" for marker in result["events"]))
        self.assertTrue(any(marker.get("text") == "长上影" for marker in result["events"]))

    def test_unverified_symbol_cannot_load_a_chart(self):
        with patch.object(app, "fetch_markets", return_value=[]):
            with self.assertRaisesRegex(ValueError, "官网清单核验"):
                app.chart_data({"instId": "FAKE-USDT-SWAP", "timeframe": "1h"})


if __name__ == "__main__":
    unittest.main()
