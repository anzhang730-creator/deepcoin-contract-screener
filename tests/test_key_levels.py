import time
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

import app
from app import expand_key_level_conditions
from key_levels import DEFAULT_SETTINGS, KeyLevelCalculator, Zone, _atr_values, calculate_reference_levels, confirmed_htf_events, encode_conditions


def bar(timestamp, open_price, high, low, close, volume=1.0):
    return [timestamp, open_price, high, low, close, volume]


def test_settings():
    return dict(
        DEFAULT_SETTINGS,
        atrLength=2,
        pivotLeft=1,
        pivotRight=1,
        minSwingAtr=0.1,
        zoneAtrMult=0.3,
        maxZonePct=10.0,
        mergeAtrMult=0.1,
        minCreateScore=40.0,
        maxZonesPerSide=4,
        touchCooldown=2,
        reactionAtr=0.2,
        reactionBars=3,
        breakBufferAtr=0.0,
        breakClosesNeed=2,
        useHtf=False,
    )


class KeyLevelCalculatorTests(unittest.TestCase):
    def test_atr_uses_wilder_smoothing_seeded_by_sma(self):
        candles = [
            bar(0, 9, 10, 8, 9),
            bar(1, 9, 11, 9, 10),
            bar(2, 10, 12, 9, 11),
        ]
        values = _atr_values(candles, 2)
        self.assertIsNone(values[0])
        self.assertEqual(values[1], 2.0)
        self.assertEqual(values[2], 2.5)

    def test_equal_extremes_choose_the_rightmost_pivot(self):
        highs = [bar(i, 10, value, 8, 9) for i, value in enumerate([10, 12, 12, 11])]
        lows = [bar(i, 10, 12, value, 10) for i, value in enumerate([9, 7, 7, 8])]
        self.assertIsNone(KeyLevelCalculator._pivot(highs, 1, 2, True))
        self.assertEqual(KeyLevelCalculator._pivot(highs[1:], 1, 1, True), 12)
        self.assertIsNone(KeyLevelCalculator._pivot(lows, 1, 2, False))
        self.assertEqual(KeyLevelCalculator._pivot(lows[1:], 1, 1, False), 7)

    def test_confirmed_pivot_creates_zone_then_touch_and_break_events(self):
        candles = [
            bar(i * 3_600_000, *values)
            for i, values in enumerate([
                (9, 10, 8, 9), (9, 10, 8, 9), (11, 14, 9, 11), (9, 10, 8, 9),
                (13.6, 13.9, 13.2, 13.6), (12.4, 13.2, 12, 12.4),
                (15, 15.1, 14.8, 15), (15, 15.2, 14.9, 15),
            ])
        ]
        calculator = KeyLevelCalculator(test_settings())
        snapshots = [calculator.process([candle]) for candle in candles]

        resistance = next(zone for zone in snapshots[3]["zones"] if zone["kind"] == -1)
        self.assertAlmostEqual(resistance["top"], 14.3675)
        self.assertAlmostEqual(resistance["bottom"], 13.3175)
        self.assertTrue(snapshots[4]["events"]["resistanceTouch"])
        self.assertTrue(snapshots[7]["events"]["resistanceBroken"])
        self.assertFalse(any(zone["kind"] == -1 for zone in snapshots[7]["zones"]))

    def test_support_hammer_requires_rejection_at_an_active_support_zone(self):
        calculator = KeyLevelCalculator(test_settings())
        calculator._add_candidate(100.0, 1, 2.0, 1.0, False, 0, 101.0, 2.0, 0)
        support = calculator.zones[0]
        hammer = bar(0, 100.3, 101.2, 98.9, 101.0)

        self.assertTrue(calculator._is_support_hammer(hammer, support, 4.0))
        self.assertFalse(calculator._is_support_hammer(hammer, None, 4.0))
        self.assertFalse(calculator._is_support_hammer(
            bar(0, 100.3, 101.2, 98.9, 100.4), support, 4.0,
        ))
        self.assertFalse(calculator._is_support_hammer(
            bar(0, 100.3, 101.2, 97.0, 101.0), support, 4.0,
        ))

    @staticmethod
    def _zone(kind, top, bottom, score=70.0):
        return Zone(
            top=top, bottom=bottom, kind=kind, score=score, touches=1,
            born_bar=0, origin_bar=0, origin_price=(top + bottom) * 0.5,
            last_touch_bar=0, pending_touch_bar=0, outside_closes=0,
            confluence=1, pending_reaction=False, awaiting_retest=False,
            contains_htf=False,
        )

    def test_relaxed_support_hammer_still_requires_zone_touch_and_reclaim(self):
        calculator = KeyLevelCalculator(test_settings())
        support = self._zone(1, 101.0, 99.0)
        loose_hammer = bar(0, 99.6, 100.8, 96.8, 100.0)

        self.assertTrue(calculator._is_support_hammer(loose_hammer, support, 4.0))
        self.assertFalse(calculator._is_support_hammer(loose_hammer, None, 4.0))
        self.assertFalse(calculator._is_support_hammer(
            bar(0, 99.6, 100.8, 96.8, 99.8), support, 4.0,
        ))

    def test_resistance_inverted_hammer_is_a_mirrored_rejection(self):
        calculator = KeyLevelCalculator(test_settings())
        resistance = self._zone(-1, 101.0, 99.0, score=66.0)
        loose_inverted = bar(0, 100.4, 103.2, 99.2, 100.0)

        self.assertTrue(calculator._is_resistance_inverted_hammer(loose_inverted, resistance, 4.0))
        self.assertFalse(calculator._is_resistance_inverted_hammer(loose_inverted, None, 4.0))
        self.assertFalse(calculator._is_resistance_inverted_hammer(
            bar(0, 100.4, 103.2, 99.2, 100.2), resistance, 4.0,
        ))
        self.assertFalse(calculator._is_resistance_inverted_hammer(
            bar(0, 100.4, 104.0, 99.2, 100.0), resistance, 4.0,
        ))

    def test_hammer_checks_all_touched_support_zones_and_uses_matching_score(self):
        calculator = KeyLevelCalculator(dict(test_settings(), atrLength=1))
        nearer_but_not_reclaimed = self._zone(1, 101.0, 99.8, score=90.0)
        farther_valid_support = self._zone(1, 99.0, 98.0, score=68.0)
        hammer = bar(0, 99.6, 100.8, 96.8, 100.0)
        calculator.zones.extend([nearer_but_not_reclaimed, farther_valid_support])

        snapshot = calculator.process([hammer])

        self.assertTrue(snapshot["events"]["supportHammer"])
        self.assertEqual(snapshot["supportHammerScore"], 68.0)
        self.assertEqual(calculator._support_hammer_history[-1]["supportScore"], 68.0)

    def test_long_upper_wick_checks_all_touched_resistance_zones_and_uses_matching_score(self):
        calculator = KeyLevelCalculator(dict(test_settings(), atrLength=1))
        nearer_but_not_rejected = self._zone(-1, 100.0, 99.2, score=90.0)
        farther_valid_resistance = self._zone(-1, 102.0, 100.2, score=67.0)
        candle = bar(0, 100.4, 103.2, 99.2, 100.0)
        calculator.zones.extend([nearer_but_not_rejected, farther_valid_resistance])

        snapshot = calculator.process([candle])

        self.assertTrue(snapshot["events"]["resistanceInvertedHammer"])
        self.assertEqual(snapshot["resistanceLongUpperWickScore"], 67.0)
        self.assertEqual(calculator._resistance_inverted_hammer_history[-1]["resistanceScore"], 67.0)

    def test_pivot_events_are_aligned_to_the_confirming_higher_timeframe_close(self):
        htf_duration = 4 * 3_600_000
        htf = [
            bar(0, 9, 10, 8, 9),
            bar(htf_duration, 12, 15, 10, 12),
            bar(2 * htf_duration, 11, 12, 9, 10),
        ]
        base = [bar(index * 3_600_000, 10, 15 if index == 6 else 11, 9, 10)
                for index in range(12)]
        config = dict(test_settings(), useHtf=True, atrLength=2, pivotLeft=20, pivotRight=20,
                      htfPivotLeft=1, htfPivotRight=1)

        events = confirmed_htf_events(htf, base, 3_600_000, htf_duration, config)

        self.assertNotIn(10, events)
        self.assertIn(11, events)
        self.assertEqual(events[11][0][0], -1)

    def test_htf_pivot_is_exposed_only_on_matching_chart_bar_close(self):
        htf = [
            bar(0, 9, 10, 8, 9),
            bar(4 * 3_600_000, 12, 15, 10, 12),
            bar(8 * 3_600_000, 11, 12, 9, 10),
        ]
        base = [bar(index * 3_600_000, 10, 15 if index == 6 else 11, 9, 10)
                for index in range(12)]
        config = dict(test_settings(), useHtf=True, atrLength=2, pivotLeft=20, pivotRight=20,
                      htfPivotLeft=1, htfPivotRight=1)
        events = confirmed_htf_events(htf, base, 3_600_000, 4 * 3_600_000, config)
        self.assertIn(11, events)
        self.assertEqual(events[11][0][0], -1)
        self.assertEqual(events[11][0][1], 15)
        self.assertEqual(events[11][0][4], 6)

        snapshot = KeyLevelCalculator(config).process(base, events)
        resistance = next(zone for zone in snapshot["zones"] if zone["kind"] == -1)
        self.assertEqual(resistance["bornBar"], 11)
        self.assertEqual(resistance["originBar"], 6)
        self.assertEqual(resistance["originPrice"], 15)

    def test_htf_pivot_without_chart_source_is_not_anchored_to_confirmation_bar(self):
        htf = [
            bar(0, 9, 10, 8, 9),
            bar(4 * 3_600_000, 12, 15, 10, 12),
            bar(8 * 3_600_000, 11, 12, 9, 10),
        ]
        base = [bar(11 * 3_600_000, 10, 11, 9, 10)]
        config = dict(test_settings(), useHtf=True, atrLength=2, htfPivotLeft=1, htfPivotRight=1)

        events = confirmed_htf_events(htf, base, 3_600_000, 4 * 3_600_000, config)
        snapshot = KeyLevelCalculator(config).process(base, events)

        resistance = next(zone for zone in snapshot["zones"] if zone["kind"] == -1)
        self.assertEqual(resistance["bornBar"], 0)
        self.assertEqual(resistance["originBar"], -1)
        self.assertIsNone(resistance["originPrice"])

    def test_merged_zone_uses_the_source_pivot_nearest_its_updated_center(self):
        config = dict(test_settings(), mergeAtrMult=0.5)
        calculator = KeyLevelCalculator(config)

        calculator._add_candidate(10.0, -1, 1.0, 0.1, False, 4, 10.0, 1.0, 4, 4)
        calculator._add_candidate(10.4, -1, 1.0, 2.0, True, 12, 10.4, 1.0, 12, 12)

        zone = calculator._zone_dict(calculator.zones[0])
        self.assertEqual(zone["bornBar"], 4)
        self.assertEqual(zone["originBar"], 12)
        self.assertEqual(zone["originPrice"], 10.4)

    def test_live_features_reselect_nearest_zone_and_do_not_replay_close_events(self):
        snapshot = {
            "price": 10.0,
            "atr": 1.0,
            "events": {"supportTouch": True, "supportHammer": True},
            "zones": [
                {"top": 8.5, "bottom": 7.5, "kind": 1, "score": 60, "touches": 0, "confluence": 1},
                {"top": 11.5, "bottom": 10.5, "kind": 1, "score": 70, "touches": 1, "confluence": 2},
            ],
        }
        closed = encode_conditions(snapshot, 11.0)
        live = encode_conditions(snapshot, 8.0, live=True)
        self.assertEqual(closed["kl_support_top"], 11.5)
        self.assertEqual(closed["kl_event_support_touch"], 1.0)
        self.assertEqual(closed["kl_event_support_hammer"], 1.0)
        self.assertEqual(live["kl_support_top"], 8.5)
        self.assertEqual(live["kl_event_support_touch"], 0.0)
        self.assertEqual(live["kl_event_support_hammer"], 0.0)

    def test_reference_levels_match_previous_periods_and_session_vwap(self):
        day = 86_400_000
        daily = [
            bar(0, 9, 12, 8, 11, 100),
            bar(day, 11, 15, 10, 14, 200),
        ]
        weekly = [
            bar(-7 * day, 10, 20, 5, 15, 1000),
            bar(0, 15, 25, 7, 18, 1200),
        ]
        intraday = [
            bar(day, 11, 14, 10, 13, 2),
            bar(day + 3_600_000, 13, 15, 12, 14, 4),
        ]

        values = calculate_reference_levels(intraday, daily, weekly, 3_600_000)

        self.assertEqual(values["kl_previous_day_high"], 12)
        self.assertEqual(values["kl_previous_day_low"], 8)
        self.assertEqual(values["kl_previous_week_high"], 20)
        self.assertEqual(values["kl_previous_week_low"], 5)
        self.assertEqual(values["kl_day_open"], 11)
        self.assertAlmostEqual(values["kl_session_vwap"], (12.3333333333 * 2 + 13.6666666667 * 4) / 6)

    def test_reference_features_are_returned_without_dynamic_zone_snapshot(self):
        values = encode_conditions(None, references={"kl_day_open": 12.5})
        self.assertEqual(values, {"kl_day_open": 12.5})


class CandleHistoryTests(unittest.TestCase):
    def setUp(self):
        with app._candle_cache_lock:
            app._candle_cache.clear()
            app._candle_cache_coverage.clear()
        with app._key_level_snapshot_cache_lock:
            app._key_level_snapshot_cache.clear()

    def test_long_history_paginates_and_is_cached(self):
        duration = app.TIMEFRAMES["1m"]
        now = int(time.time() * 1000)
        latest_open = (now // duration) * duration - duration
        history = [bar(latest_open - i * duration, 100, 101, 99, 100, 5) for i in range(1010)]
        calls = []

        def fake_get(_path, params):
            calls.append(dict(params))
            limit = int(params["limit"])
            after = params.get("after")
            rows = [row for row in history if after is None or row[0] < int(after)]
            return [list(map(str, row)) for row in rows[:limit]]

        with tempfile.TemporaryDirectory() as directory, patch.object(
            app, "CANDLE_CACHE_PATH", Path(directory) / "candles.sqlite3"
        ), patch.object(app, "deepcoin_get", side_effect=fake_get):
            candles = app.fetch_candles("TEST-PERP", "1m", limit_override=1005)
            with app._candle_cache_lock:
                app._candle_cache.clear()
            cached = app.fetch_candles("TEST-PERP", "1m", limit_override=1005)

        self.assertEqual(len(candles), 1005)
        self.assertEqual(candles, cached)
        self.assertEqual(len(calls), 2)
        self.assertEqual(calls[0]["limit"], 1000)
        self.assertIsNotNone(calls[1]["after"])

    def test_short_current_history_is_cached_without_refetching(self):
        duration = app.TIMEFRAMES["1h"]
        now = int(time.time() * 1000)
        latest_open = (now // duration) * duration - duration
        history = [bar(latest_open - i * duration, 100, 101, 99, 100, 5) for i in range(20)]
        calls = []

        def fake_get(_path, params):
            calls.append(dict(params))
            return [list(map(str, row)) for row in history[:int(params["limit"])]]

        with tempfile.TemporaryDirectory() as directory, patch.object(
            app, "CANDLE_CACHE_PATH", Path(directory) / "candles.sqlite3"
        ), patch.object(app, "deepcoin_get", side_effect=fake_get):
            first = app.fetch_candles("NEW-PERP", "1h", limit_override=100)
            second = app.fetch_candles("NEW-PERP", "1h", limit_override=100)

        self.assertEqual(len(first), len(history))
        self.assertEqual(second, first)
        # A short first page is followed once to distinguish a genuinely new
        # listing from a gateway that returned only the newest few candles.
        self.assertEqual(len(calls), 2)

    def test_short_first_page_continues_until_requested_history_is_collected(self):
        duration = app.TIMEFRAMES["1h"]
        now = int(time.time() * 1000)
        latest_open = (now // duration) * duration - duration
        history = [bar(latest_open - i * duration, 100, 101, 99, 100 + i, 5) for i in range(12)]
        calls = []

        def fake_get(_path, params):
            calls.append(dict(params))
            limit = int(params["limit"])
            after = params.get("after")
            rows = [row for row in history if after is None or row[0] < int(after)]
            if after is None:
                rows = rows[:3]
            return [list(map(str, row)) for row in rows[:limit]]

        with tempfile.TemporaryDirectory() as directory, patch.object(
            app, "CANDLE_CACHE_PATH", Path(directory) / "candles.sqlite3"
        ), patch.object(app, "deepcoin_get", side_effect=fake_get):
            candles = app.fetch_candles("SHORT-PERP", "1h", limit_override=8)

        self.assertEqual(len(candles), 8)
        self.assertGreaterEqual(len(calls), 2)
        self.assertIsNotNone(calls[1].get("after"))

    def test_key_level_snapshot_cache_reuses_only_identical_candle_data(self):
        bars = [bar(i * 3_600_000, 100, 101, 99, 100, 5) for i in range(40)]
        htf_bars = [bar(i * 14_400_000, 100, 102, 98, 100, 20) for i in range(20)]
        with patch.object(app, "calculate_key_levels", return_value={"snapshot": 1}) as calculate:
            first = app.cached_key_level_snapshot("TEST-PERP", "1h", bars, htf_bars, 3_600_000, 14_400_000, 0.01)
            second = app.cached_key_level_snapshot("TEST-PERP", "1h", bars, htf_bars, 3_600_000, 14_400_000, 0.01)
            changed_bars = [list(row) for row in bars]
            changed_bars[-1][4] += 0.1
            third = app.cached_key_level_snapshot("TEST-PERP", "1h", changed_bars, htf_bars, 3_600_000, 14_400_000, 0.01)

        self.assertEqual(first, second)
        self.assertEqual(third, first)
        self.assertEqual(calculate.call_count, 2)


class ScreenConditionTests(unittest.TestCase):
    def test_recursive_indicator_history_matches_chart_warmup_length(self):
        short_condition = {"indicator": "ema", "period": 20}
        long_condition = {"indicator": "rsi", "period": 200}

        self.assertEqual(app.condition_period(short_condition) + 30, app.MIN_INDICATOR_HISTORY)
        self.assertEqual(app.condition_period(long_condition) + 30, 2000)

    def test_period_200_screen_requests_the_same_warmup_window_as_chart(self):
        market = {
            "id": "TEST-USDT-SWAP", "symbol": "TESTUSDT", "base": "TEST", "quote": "USDT",
            "assetType": "linear", "webCatalogStatus": "available", "webListed": True,
        }
        bars = [bar(i * 3_600_000, 100 + i * 0.1, 101 + i * 0.1,
                    99 + i * 0.1, 100 + i * 0.1 + (i % 7) * 0.01, 1)
                for i in range(2000)]
        condition = {
            "timeframe": "1h", "indicator": "ema", "period": 200, "operator": ">",
            "valueType": "number", "value": 100,
        }
        with patch.object(app, "fetch_markets", return_value=[market]), \
             patch.object(app, "fetch_tickers", return_value={market["id"]: {"last": 310.0, "volume24h": 100}}), \
             patch.object(app, "fetch_candles", return_value=bars) as fetch_candles:
            result = app.screen({"quote": "USDT", "timeframe": "1h", "maxSymbols": 1,
                                 "conditions": [condition]})

        self.assertEqual(fetch_candles.call_args.args[2], 1970)
        self.assertEqual(len(result["closeResults"]), 1)

    def setUp(self):
        with app._key_level_snapshot_cache_lock:
            app._key_level_snapshot_cache.clear()

    def test_price_within_indicator_percent_uses_close_and_live_prices_separately(self):
        market = {
            "id": "TEST-USDT-SWAP", "symbol": "TESTUSDT", "base": "TEST", "quote": "USDT",
            "assetType": "linear", "webCatalogStatus": "available", "webListed": True,
        }
        bars = [bar(i * 3_600_000, 100, 101, 99, 100, 1) for i in range(50)]
        condition = {
            "timeframe": "1h", "indicator": "price", "operator": "within_percent",
            "valueType": "indicator", "valueIndicator": "ema", "valuePeriod": 20,
            "value": 0.5,
        }
        with patch.object(app, "fetch_markets", return_value=[market]), \
             patch.object(app, "fetch_tickers", return_value={market["id"]: {"last": 102.0, "volume24h": 100}}), \
             patch.object(app, "fetch_candles", return_value=bars) as candle_fetch:
            result = app.screen({"quote": "USDT", "timeframe": "1h", "maxSymbols": 1,
                                 "conditions": [condition]})

        self.assertEqual(len(result["closeResults"]), 1)
        self.assertEqual(result["liveResults"], [])
        self.assertEqual(candle_fetch.call_args.args[2], app.MIN_INDICATOR_HISTORY - 30)

    def test_within_percent_condition_only_accepts_price_scale_indicators(self):
        app.validate_conditions([{
            "indicator": "price", "operator": "within_percent", "valueType": "indicator",
            "valueIndicator": "bb_lower", "valuePeriod": 20, "value": 1.5,
        }])
        with self.assertRaisesRegex(ValueError, "最新价"):
            app.validate_conditions([{
                "indicator": "rsi", "operator": "within_percent", "valueType": "indicator",
                "valueIndicator": "ema", "valuePeriod": 20, "value": 1.5,
            }])

    def test_closed_event_filter_is_not_reused_for_live_price_results(self):
        market = {
            "id": "TEST-USDT-SWAP", "symbol": "TESTUSDT", "base": "TEST", "quote": "USDT",
            "assetType": "linear", "webCatalogStatus": "available", "webListed": True,
        }
        bars = [bar(i * 3_600_000, 9, 11, 8, 10, 1) for i in range(40)]
        snapshot = {
            "price": 10.0,
            "atr": 1.0,
            "events": {"supportTouch": True},
            "zones": [{"top": 9.5, "bottom": 8.5, "kind": 1, "score": 80, "touches": 1, "confluence": 1}],
        }
        condition = {
            "timeframe": "1h", "indicator": "key_levels", "keyLevelSignal": "support_touch",
        }
        with patch.object(app, "fetch_markets", return_value=[market]), \
             patch.object(app, "fetch_tickers", return_value={market["id"]: {"last": 12.0, "volume24h": 100}}), \
             patch.object(app, "fetch_candles", return_value=bars) as candle_fetch, \
             patch.object(app, "calculate_key_levels", return_value=snapshot):
            result = app.screen({"quote": "USDT", "timeframe": "1h", "maxSymbols": 1, "conditions": [condition]})

        self.assertEqual(len(result["closeResults"]), 1)
        self.assertEqual(result["closeResults"][0]["keyLevelsByTimeframe"]["1h"]["kl_event_support_touch"], 1.0)
        self.assertEqual(result["liveResults"], [])
        fetched_periods = {call.args[1]: call.args[2] for call in candle_fetch.call_args_list}
        self.assertGreaterEqual(fetched_periods["1h"], app.KEY_LEVEL_BASE_HISTORY - 30)
        expected_htf_period = (
            app.KEY_LEVEL_SETTINGS["maxZoneAge"] * app.TIMEFRAMES["1h"] // app.TIMEFRAMES["4h"]
            + app.KEY_LEVEL_HTF_WARMUP - 30
        )
        self.assertGreaterEqual(fetched_periods["4h"], expected_htf_period)

    def test_support_hammer_rejection_uses_closed_candle_event_and_quality_threshold(self):
        market = {
            "id": "TEST-USDT-SWAP", "symbol": "TESTUSDT", "base": "TEST", "quote": "USDT",
            "assetType": "linear", "webCatalogStatus": "available", "webListed": True,
        }
        bars = [bar(i * 3_600_000, 9, 11, 8, 10, 1) for i in range(40)]
        snapshot = {
            "price": 10.0, "atr": 1.0,
            "events": {"supportHammer": True},
            "zones": [{"top": 9.5, "bottom": 8.5, "kind": 1, "score": 80,
                       "touches": 1, "confluence": 2}],
        }
        condition = {
            "timeframe": "1h", "indicator": "key_levels",
            "keyLevelSignal": "support_hammer_rejection", "keyLevelThreshold": 78,
            "keyLevelLookbackBars": 1,
        }
        with patch.object(app, "fetch_markets", return_value=[market]), \
             patch.object(app, "fetch_tickers", return_value={market["id"]: {"last": 12.0, "volume24h": 100}}), \
             patch.object(app, "fetch_candles", return_value=bars), \
             patch.object(app, "calculate_key_levels", return_value=snapshot):
            result = app.screen({"quote": "USDT", "timeframe": "1h", "maxSymbols": 1,
                                 "conditions": [condition]})

        self.assertEqual(len(result["closeResults"]), 1)
        self.assertEqual(result["closeResults"][0]["keyLevelsByTimeframe"]["1h"]["kl_event_support_hammer"], 1.0)
        self.assertEqual(result["liveResults"], [])

    def test_support_hammer_scan_can_match_a_recent_closed_event(self):
        market = {
            "id": "TEST-USDT-SWAP", "symbol": "TESTUSDT", "base": "TEST", "quote": "USDT",
            "assetType": "linear", "webCatalogStatus": "available", "webListed": True,
        }
        bars = [bar(i * 3_600_000, 9, 11, 8, 10, 1) for i in range(40)]
        snapshot = {
            "price": 10.0, "atr": 1.0,
            "events": {"supportHammer": False},
            "supportHammerHistory": [
                {"event": True, "supportScore": 80.0},
                {"event": False, "supportScore": 60.0},
            ],
            "zones": [{"top": 9.5, "bottom": 8.5, "kind": 1, "score": 60,
                       "touches": 1, "confluence": 2}],
        }
        condition = {
            "timeframe": "1h", "indicator": "key_levels",
            "keyLevelSignal": "support_hammer_rejection", "keyLevelThreshold": 75,
            "keyLevelLookbackBars": 6,
        }
        with patch.object(app, "fetch_markets", return_value=[market]), \
             patch.object(app, "fetch_tickers", return_value={market["id"]: {"last": 10.5, "volume24h": 100}}), \
             patch.object(app, "fetch_candles", return_value=bars), \
             patch.object(app, "calculate_key_levels", return_value=snapshot):
            result = app.screen({"quote": "USDT", "timeframe": "1h", "maxSymbols": 1,
                                 "conditions": [condition]})

        self.assertEqual(len(result["closeResults"]), 1)
        self.assertEqual(result["closeResults"][0]["keyLevelsByTimeframe"]["1h"]["kl_support_score"], 60)
        self.assertEqual(result["closeResults"][0]["keyLevelsByTimeframe"]["1h"]["kl_event_support_hammer"], 0.0)
        self.assertEqual(result["liveResults"], [])

    def test_inverted_hammer_scan_matches_a_recent_closed_resistance_event(self):
        market = {
            "id": "TEST-USDT-SWAP", "symbol": "TESTUSDT", "base": "TEST", "quote": "USDT",
            "assetType": "linear", "webCatalogStatus": "available", "webListed": True,
        }
        bars = [bar(i * 3_600_000, 9, 11, 8, 10, 1) for i in range(40)]
        snapshot = {
            "price": 10.0, "atr": 1.0,
            "events": {"resistanceInvertedHammer": False},
            "resistanceInvertedHammerHistory": [
                {"event": True, "resistanceScore": 64.0},
                {"event": False, "resistanceScore": 58.0},
            ],
            "zones": [{"top": 11.5, "bottom": 10.5, "kind": -1, "score": 58,
                       "touches": 1, "confluence": 1}],
        }
        condition = {
            "timeframe": "1h", "indicator": "key_levels",
            "keyLevelSignal": "resistance_inverted_hammer_rejection", "keyLevelThreshold": 60,
            "keyLevelLookbackBars": 6,
        }
        with patch.object(app, "fetch_markets", return_value=[market]), \
             patch.object(app, "fetch_tickers", return_value={market["id"]: {"last": 10.5, "volume24h": 100}}), \
             patch.object(app, "fetch_candles", return_value=bars), \
             patch.object(app, "calculate_key_levels", return_value=snapshot):
            result = app.screen({"quote": "USDT", "timeframe": "1h", "maxSymbols": 1,
                                 "conditions": [condition]})

        self.assertEqual(len(result["closeResults"]), 1)
        self.assertEqual(result["liveResults"], [])

    def test_one_key_level_condition_expands_to_its_internal_checks(self):
        condition = {
            "timeframe": "4h", "indicator": "key_levels",
            "keyLevelSignal": "high_quality_support", "keyLevelThreshold": 72,
        }

        expanded = expand_key_level_conditions([condition])

        self.assertEqual(len(expanded), 2)
        self.assertEqual([item["indicator"] for item in expanded], ["kl_support_exists", "kl_support_score"])
        self.assertEqual(expanded[1]["operator"], ">=")
        self.assertEqual(expanded[1]["value"], 72)
        self.assertEqual({item["timeframe"] for item in expanded}, {"4h"})

    def test_support_hammer_condition_expands_to_event_zone_and_score(self):
        condition = {
            "timeframe": "1h", "indicator": "key_levels",
            "keyLevelSignal": "support_hammer_rejection", "keyLevelThreshold": 78,
            "keyLevelLookbackBars": 6,
        }

        expanded = expand_key_level_conditions([condition])

        self.assertEqual([item["indicator"] for item in expanded], [
            app.KEY_LEVEL_RECENT_HAMMER_FEATURE,
        ])
        self.assertEqual(expanded[0]["keyLevelLookbackBars"], 6)
        self.assertEqual(expanded[0]["keyLevelThreshold"], 78)
        self.assertTrue(all(item["timeframe"] == "1h" for item in expanded))

    def test_inverted_hammer_condition_expands_to_recent_closed_resistance_event(self):
        expanded = expand_key_level_conditions([{
            "timeframe": "4h", "indicator": "key_levels",
            "keyLevelSignal": "resistance_inverted_hammer_rejection",
            "keyLevelThreshold": 60, "keyLevelLookbackBars": 12,
        }])

        self.assertEqual(len(expanded), 1)
        self.assertEqual(expanded[0]["indicator"], app.KEY_LEVEL_RECENT_INVERTED_HAMMER_FEATURE)
        self.assertEqual(expanded[0]["keyLevelLookbackBars"], 12)
        self.assertEqual(expanded[0]["keyLevelThreshold"], 60)

    def test_recent_inverted_hammer_uses_event_time_resistance_score(self):
        condition = expand_key_level_conditions([{
            "timeframe": "1h", "indicator": "key_levels",
            "keyLevelSignal": "resistance_inverted_hammer_rejection", "keyLevelThreshold": 60,
            "keyLevelLookbackBars": 2,
        }])[0]
        metrics = {"_resistanceInvertedHammerHistory": [
            {"event": True, "resistanceScore": 64},
            {"event": False, "resistanceScore": 70},
        ]}

        self.assertTrue(app.evaluate_condition(condition, metrics, []))
        condition["keyLevelLookbackBars"] = 1
        self.assertFalse(app.evaluate_condition(condition, metrics, []))
        metrics["_resistanceInvertedHammerHistory"][0]["resistanceScore"] = 59
        condition["keyLevelLookbackBars"] = 2
        self.assertFalse(app.evaluate_condition(condition, metrics, []))

    def test_recent_hammer_signal_uses_event_time_support_score(self):
        condition = expand_key_level_conditions([{
            "timeframe": "1h", "indicator": "key_levels",
            "keyLevelSignal": "support_hammer_rejection", "keyLevelThreshold": 75,
            "keyLevelLookbackBars": 6,
        }])[0]
        metrics = {"_supportHammerHistory": [
            {"event": True, "supportScore": 80},
            {"event": False, "supportScore": 60},
        ]}

        self.assertTrue(app.evaluate_condition(condition, metrics, []))
        condition["keyLevelLookbackBars"] = 1
        self.assertFalse(app.evaluate_condition(condition, metrics, []))
        metrics["_supportHammerHistory"][0]["supportScore"] = 70
        condition["keyLevelLookbackBars"] = 6
        self.assertFalse(app.evaluate_condition(condition, metrics, []))

    def test_reference_only_scan_uses_short_history_and_returns_live_values(self):
        market = {
            "id": "TEST-USDT-SWAP", "symbol": "TESTUSDT", "base": "TEST", "quote": "USDT",
            "assetType": "linear", "webCatalogStatus": "available", "webListed": True,
        }
        day = 86_400_000
        base = [bar(day + i * 3_600_000, 11, 14, 10, 13, 2) for i in range(24)]
        daily = [bar(0, 9, 12, 8, 11, 100), bar(day, 11, 15, 10, 14, 200)]
        weekly = [bar(-7 * day, 10, 20, 5, 15, 1000), bar(0, 15, 25, 7, 18, 1200)]

        def recent(_inst_id, timeframe, _limit):
            return {"1h": base[-3:], "1d": daily, "1w": weekly}[timeframe]

        condition = {
            "timeframe": "1h", "indicator": "kl_previous_day_high", "operator": ">",
            "valueType": "number", "value": 11.5,
        }
        with patch.object(app, "fetch_markets", return_value=[market]), \
             patch.object(app, "fetch_tickers", return_value={market["id"]: {"last": 13.5, "volume24h": 100}}), \
             patch.object(app, "fetch_candles", return_value=base) as candle_fetch, \
             patch.object(app, "fetch_recent_candles", side_effect=recent):
            result = app.screen({"quote": "USDT", "timeframe": "1h", "maxSymbols": 1, "conditions": [condition]})

        self.assertEqual(len(result["closeResults"]), 1)
        self.assertEqual(len(result["liveResults"]), 1)
        self.assertEqual(result["closeResults"][0]["kl_previous_day_high"], 12)
        self.assertEqual(result["liveResults"][0]["kl_previous_day_high"], 12)
        self.assertEqual(result["liveResults"][0]["keyLevelsByTimeframe"]["1h"]["kl_previous_day_high"], 12)
        self.assertNotIn("4h", [call.args[1] for call in candle_fetch.call_args_list])
        self.assertLess(max(call.args[2] for call in candle_fetch.call_args_list), app.KEY_LEVEL_BASE_HISTORY)

    def test_live_result_top_level_uses_zone_nearest_to_live_price(self):
        market = {
            "id": "TEST-USDT-SWAP", "symbol": "TESTUSDT", "base": "TEST", "quote": "USDT",
            "assetType": "linear", "webCatalogStatus": "available", "webListed": True,
        }
        bars = [bar(i * 3_600_000, 9, 11, 8, 10, 1) for i in range(40)]
        snapshot = {
            "price": 10.0, "atr": 1.0, "events": {},
            "zones": [
                {"top": 9.5, "bottom": 8.5, "kind": 1, "score": 60, "touches": 0, "confluence": 1},
                {"top": 12.5, "bottom": 11.5, "kind": 1, "score": 70, "touches": 1, "confluence": 2},
            ],
        }
        condition = {
            "timeframe": "1h", "indicator": "kl_support_top", "operator": ">",
            "valueType": "number", "value": 0,
        }
        with patch.object(app, "fetch_markets", return_value=[market]), \
             patch.object(app, "fetch_tickers", return_value={market["id"]: {"last": 12.0, "volume24h": 100}}), \
             patch.object(app, "fetch_candles", return_value=bars), \
             patch.object(app, "calculate_key_levels", return_value=snapshot):
            result = app.screen({"quote": "USDT", "timeframe": "1h", "maxSymbols": 1, "conditions": [condition]})

        self.assertEqual(result["closeResults"][0]["kl_support_top"], 9.5)
        self.assertEqual(result["liveResults"][0]["kl_support_top"], 12.5)
        self.assertEqual(result["liveResults"][0]["keyLevelsByTimeframe"]["1h"]["kl_support_top"], 12.5)


if __name__ == "__main__":
    unittest.main()
