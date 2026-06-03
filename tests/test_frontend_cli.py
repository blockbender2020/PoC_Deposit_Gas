import io
import sys
import unittest
from pathlib import Path
from unittest.mock import patch
from urllib.error import HTTPError


sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "scripts"))

import frontend_cli


class ApiClientTests(unittest.TestCase):
    def _http_error(self, url: str, code: int, body: bytes) -> HTTPError:
        return HTTPError(url, code, "error", hdrs=None, fp=io.BytesIO(body))

    @patch("frontend_cli.urlopen")
    def test_remote_http_502_suggests_https(self, mock_urlopen) -> None:
        mock_urlopen.side_effect = self._http_error("http://gasless.enigma.bz/health", 502, b"")

        client = frontend_cli.ApiClient("http://gasless.enigma.bz/")

        with self.assertRaises(RuntimeError) as excinfo:
            client.get("/health")

        self.assertEqual(
            str(excinfo.exception),
            "HTTP 502: Response body was empty. Remote deployments usually require HTTPS. Try https://gasless.enigma.bz",
        )

    @patch("frontend_cli.urlopen")
    def test_local_http_502_does_not_suggest_https(self, mock_urlopen) -> None:
        mock_urlopen.side_effect = self._http_error("http://127.0.0.1:3000/health", 502, b"")

        client = frontend_cli.ApiClient("http://127.0.0.1:3000")

        with self.assertRaises(RuntimeError) as excinfo:
            client.get("/health")

        self.assertEqual(str(excinfo.exception), "HTTP 502: Response body was empty")
