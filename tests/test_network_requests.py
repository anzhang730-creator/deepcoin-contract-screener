import json
import ssl
import unittest
from urllib.error import HTTPError, URLError
from unittest.mock import patch

import app


class Response:
    def __enter__(self):
        return self

    def __exit__(self, *_args):
        return False

    def read(self):
        return json.dumps({"code": "0", "data": []}).encode("utf-8")


class NetworkRequestTests(unittest.TestCase):
    def test_connection_refused_fails_without_redundant_retries(self):
        failure = URLError(ConnectionRefusedError("refused"))
        with patch.object(app, "build_opener") as build_opener:
            build_opener.return_value.open.side_effect = failure
            with self.assertRaises(app.DeepcoinError) as raised:
                app.deepcoin_get("/deepcoin/market/instruments", {"instType": "SWAP"})

        self.assertEqual(build_opener.return_value.open.call_count, 1)
        self.assertIn("连接被拒绝", str(raised.exception))
        self.assertIn("api.deepcoin.com", str(raised.exception))

    def test_timeout_retries_once_then_uses_successful_response(self):
        timeout = URLError(TimeoutError("timed out"))
        with patch.object(app, "build_opener") as build_opener, patch.object(app.time, "sleep"):
            build_opener.return_value.open.side_effect = [timeout, Response()]
            result = app.deepcoin_get("/deepcoin/market/instruments", {"instType": "SWAP"})

        self.assertEqual(result, [])
        self.assertEqual(build_opener.return_value.open.call_count, 2)

    def test_tls_eof_retries_once(self):
        failure = URLError(ssl.SSLEOFError("unexpected eof"))
        with patch.object(app, "build_opener") as build_opener, patch.object(app.time, "sleep"):
            build_opener.return_value.open.side_effect = [failure, Response()]
            result = app.deepcoin_get("/deepcoin/market/instruments", {"instType": "SWAP"})

        self.assertEqual(result, [])
        self.assertEqual(build_opener.return_value.open.call_count, 2)

    def test_public_candle_http_401_retries_once(self):
        unauthorized = HTTPError(
            "https://api.deepcoin.com/deepcoin/market/candles", 401, "Unauthorized", None, None,
        )
        with patch.object(app, "build_opener") as build_opener, patch.object(app.time, "sleep"):
            build_opener.return_value.open.side_effect = [unauthorized, Response()]
            result = app.deepcoin_get("/deepcoin/market/candles", {"instId": "TEST-USDT-SWAP"})

        self.assertEqual(result, [])
        self.assertEqual(build_opener.return_value.open.call_count, 2)


if __name__ == "__main__":
    unittest.main()
