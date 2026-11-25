import time
import unittest

from market_directory import MarketDirectory, MarketDirectoryError


class MarketDirectoryTests(unittest.TestCase):
    def test_market_lookup_does_not_wait_for_background_refresh(self):
        directory = MarketDirectory(lambda: [], lambda _system: set(), lambda _systems: {})
        directory._refresh_lock.acquire()
        try:
            with directory._lock:
                directory._health.update(status="ready", refreshing=True)
            started = time.perf_counter()
            with self.assertRaisesRegex(MarketDirectoryError, "正在核验"):
                directory.get_markets()
            self.assertLess(time.perf_counter() - started, 0.1)
        finally:
            directory._refresh_lock.release()

    def test_market_lookup_reports_last_error_without_starting_another_request(self):
        calls = []
        directory = MarketDirectory(lambda: calls.append("instruments"), lambda _system: set(), lambda _systems: {})
        with directory._lock:
            directory._health.update(status="error", errors=["Deepcoin 行情接口超时"])

        started = time.perf_counter()
        with self.assertRaisesRegex(MarketDirectoryError, "Deepcoin 行情接口超时"):
            directory.get_markets()

        self.assertLess(time.perf_counter() - started, 0.1)
        self.assertEqual(calls, [])

    def test_market_lookup_uses_last_verified_markets_after_refresh_failure(self):
        instrument = {
            "ctType": "linear", "quoteCcy": "USDT", "instId": "BTC-USDT-SWAP",
            "symbol": "BTCUSDT", "state": "live", "baseCcy": "BTC",
        }
        directory = MarketDirectory(
            lambda: [instrument],
            lambda _system: {"BTCUSDT"},
            lambda systems: {system: {"BTCUSDT"} for system in systems},
        )
        confirmed = directory.get_markets(force=True)
        with directory._lock:
            directory._health.update(status="error", errors=["temporary timeout"])

        self.assertEqual(directory.get_markets(), confirmed)
        self.assertEqual(directory.health()["status"], "error")
        self.assertIn("BTCUSDT", directory.health()["verifiedSymbols"])

    def test_market_lookup_does_not_fail_while_verified_directory_refreshes(self):
        instrument = {
            "ctType": "linear", "quoteCcy": "USDT", "instId": "BTC-USDT-SWAP",
            "symbol": "BTCUSDT", "state": "live", "baseCcy": "BTC",
        }
        directory = MarketDirectory(
            lambda: [instrument],
            lambda _system: {"BTCUSDT"},
            lambda systems: {system: {"BTCUSDT"} for system in systems},
        )
        confirmed = directory.get_markets(force=True)
        with directory._lock:
            directory._checked = 0
            directory._health.update(status="ready", refreshing=True)

        self.assertEqual(directory.get_markets(), confirmed)

    def test_force_refresh_publishes_a_verified_market_for_fast_reads(self):
        instrument = {
            "ctType": "linear", "quoteCcy": "USDT", "instId": "BTC-USDT-SWAP",
            "symbol": "BTCUSDT", "state": "live", "baseCcy": "BTC",
        }
        directory = MarketDirectory(
            lambda: [instrument],
            lambda _system: {"BTCUSDT"},
            lambda systems: {system: {"BTCUSDT"} for system in systems},
        )

        markets = directory.get_markets(force=True)

        self.assertEqual([market["symbol"] for market in markets], ["BTCUSDT"])
        self.assertEqual(directory.get_markets(), markets)


if __name__ == "__main__":
    unittest.main()
