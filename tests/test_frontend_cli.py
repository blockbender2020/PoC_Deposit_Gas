import contextlib
import io
import json
import sys
import tempfile
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


class FrontendCliCommandTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temp_dir = tempfile.TemporaryDirectory()
        self.state_file = Path(self.temp_dir.name) / "state.json"

    def tearDown(self) -> None:
        self.temp_dir.cleanup()

    def _bridge_intent(self) -> dict:
        return {
            "id": "intent-1",
            "status": "AWAITING_BRIDGE",
            "ownerAccountAddress": "0x1111111111111111111111111111111111111111",
            "sourceWalletType": "solana",
            "sourceWalletAddress": "SolanaWallet1111111111111111111111111111111",
            "escrowAddress": "0x2222222222222222222222222222222222222222",
            "escrowStrategy": "shared",
            "sourceChainId": 9999,
            "sourceTokenAddress": "EPjFWdd5AufqSSqeM2qN1xzybapC8G4wEGGkZwyTDt1v",
            "destinationTokenAddress": "0x3333333333333333333333333333333333333333",
            "fromAmount": "25000000",
            "quotedDestinationAmount": "20000000000000000000",
            "minimumAmount": "20000000000000000000",
            "subAccountId": "0",
            "subAccountName": "alice-main",
            "createAccount": True,
            "targetAccountAddress": None,
            "quote": {},
            "history": [],
        }

    @patch("frontend_cli.ApiClient.post")
    def test_create_intent_uses_owner_and_source_wallet_fields(self, mock_post) -> None:
        mock_post.return_value = self._bridge_intent()

        argv = [
            "frontend_cli.py",
            "--state-file",
            str(self.state_file),
            "--raw",
            "create-intent",
            "--source-wallet-type",
            "solana",
            "--source-wallet-address",
            "SolanaWallet1111111111111111111111111111111",
            "--owner-account-address",
            "0x1111111111111111111111111111111111111111",
            "--source-chain-id",
            "9999",
            "--source-token-address",
            "EPjFWdd5AufqSSqeM2qN1xzybapC8G4wEGGkZwyTDt1v",
            "--from-amount",
            "25000000",
            "--sub-account-name",
            "alice-main",
        ]

        with contextlib.redirect_stdout(io.StringIO()), patch.object(sys, "argv", argv):
            exit_code = frontend_cli.main()

        self.assertEqual(exit_code, 0)
        mock_post.assert_called_once_with(
            "/v1/intents",
            {
                "sourceWalletType": "solana",
                "sourceWalletAddress": "SolanaWallet1111111111111111111111111111111",
                "ownerAccountAddress": "0x1111111111111111111111111111111111111111",
                "sourceChainId": 9999,
                "sourceTokenAddress": "EPjFWdd5AufqSSqeM2qN1xzybapC8G4wEGGkZwyTDt1v",
                "fromAmount": "25000000",
                "subAccountId": "0",
                "subAccountName": "alice-main",
                "createAccount": True,
            },
        )
        self.assertEqual(json.loads(self.state_file.read_text())["last_intent_id"], "intent-1")

    @patch("frontend_cli.ApiClient.post")
    def test_submit_source_tx_uses_source_tx_id(self, mock_post) -> None:
        mock_post.return_value = self._bridge_intent() | {"sourceTxId": "solana-signature-123"}
        self.state_file.write_text(json.dumps({"last_intent_id": "intent-1"}))

        argv = [
            "frontend_cli.py",
            "--state-file",
            str(self.state_file),
            "--raw",
            "submit-source-tx",
            "--source-tx-id",
            "solana-signature-123",
        ]

        with contextlib.redirect_stdout(io.StringIO()), patch.object(sys, "argv", argv):
            exit_code = frontend_cli.main()

        self.assertEqual(exit_code, 0)
        mock_post.assert_called_once_with(
            "/v1/intents/intent-1/source-tx",
            {"sourceTxId": "solana-signature-123"},
        )
