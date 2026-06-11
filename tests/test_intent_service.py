import tempfile
import unittest
from pathlib import Path

from app.models import CreateIntentRequest, SubmitSourceTxRequest
from app.service import IntentService
from app.store import IntentStore


class FakeLiFiClient:
    def __init__(self) -> None:
        self.last_quote_request = None

    def get_quote(self, request, to_address):
        self.last_quote_request = {
            "request": dict(request),
            "to_address": to_address,
        }
        return {
            "tool": "lifi",
            "estimate": {"toAmount": "20000000000000000000"},
        }

    def get_status(self, *, tx_hash, from_chain_id, to_chain_id, bridge=None):
        return {
            "status": "PENDING",
        }


class FakeHyperEvmService:
    pass


class FakeEscrowManager:
    def assign_address(self, _existing_intents):
        return ("0x2222222222222222222222222222222222222222", None)

    def get_execution_account(self, _intent):
        raise AssertionError("get_execution_account should not be called in these tests")


class IntentServiceTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temp_dir = tempfile.TemporaryDirectory()
        self.store = IntentStore(Path(self.temp_dir.name) / "intents.db")
        self.store.initialize()
        self.lifi = FakeLiFiClient()
        self.service = IntentService(
            store=self.store,
            lifi_client=self.lifi,
            hyperevm_service=FakeHyperEvmService(),
            escrow_manager=FakeEscrowManager(),
            min_collateral_amount=10,
            destination_token_address="0x3333333333333333333333333333333333333333",
            destination_chain_id=999,
            contract_config={},
        )

    def tearDown(self) -> None:
        self.temp_dir.cleanup()

    def test_create_intent_accepts_solana_source_and_owner_account(self) -> None:
        payload = CreateIntentRequest(
            sourceWalletType="solana",
            sourceWalletAddress="SolanaWallet1111111111111111111111111111111",
            ownerAccountAddress="0x1111111111111111111111111111111111111111",
            sourceChainId=1151111081099710,
            sourceTokenAddress="EPjFWdd5AufqSSqeM2qN1xzybapC8G4wEGGkZwyTDt1v",
            fromAmount="25000000",
            subAccountName="alice-main",
        )

        intent = self.service.create_intent(payload)

        self.assertEqual(intent.ownerAccountAddress, "0x1111111111111111111111111111111111111111")
        self.assertEqual(intent.sourceWalletType, "solana")
        self.assertEqual(intent.sourceWalletAddress, "SolanaWallet1111111111111111111111111111111")
        self.assertEqual(
            self.lifi.last_quote_request["request"]["sourceWalletAddress"],
            "SolanaWallet1111111111111111111111111111111",
        )
        self.assertEqual(
            self.lifi.last_quote_request["request"]["ownerAccountAddress"],
            "0x1111111111111111111111111111111111111111",
        )

    def test_submit_source_tx_accepts_solana_transaction_id(self) -> None:
        intent = self.service.create_intent(
            CreateIntentRequest(
                sourceWalletType="solana",
                sourceWalletAddress="SolanaWallet1111111111111111111111111111111",
                ownerAccountAddress="0x1111111111111111111111111111111111111111",
                sourceChainId=1151111081099710,
                sourceTokenAddress="EPjFWdd5AufqSSqeM2qN1xzybapC8G4wEGGkZwyTDt1v",
                fromAmount="25000000",
                subAccountName="alice-main",
            )
        )

        updated = self.service.submit_source_tx(
            intent.id,
            SubmitSourceTxRequest(sourceTxId="5H6YxQW9solanaSignature123"),
        )

        self.assertEqual(updated.sourceTxId, "5H6YxQW9solanaSignature123")
        self.assertEqual(updated.sourceWalletType, "solana")
        self.assertIn(updated.status, {"BRIDGE_SUBMITTED", "BRIDGE_PENDING"})

    def test_execution_context_uses_owner_account_address_as_user_address(self) -> None:
        intent = self.service.create_intent(
            CreateIntentRequest(
                sourceWalletType="solana",
                sourceWalletAddress="SolanaWallet1111111111111111111111111111111",
                ownerAccountAddress="0x1111111111111111111111111111111111111111",
                sourceChainId=1151111081099710,
                sourceTokenAddress="EPjFWdd5AufqSSqeM2qN1xzybapC8G4wEGGkZwyTDt1v",
                fromAmount="25000000",
                subAccountName="alice-main",
            )
        )
        intent = intent.model_copy(update={"receivedAmount": "20", "sourceTxId": "solana-tx-1"})

        context = self.service._build_execution_context(intent)

        self.assertEqual(context["userAddress"], "0x1111111111111111111111111111111111111111")
        self.assertEqual(context["ownerAccountAddress"], "0x1111111111111111111111111111111111111111")
        self.assertEqual(context["sourceWalletAddress"], "SolanaWallet1111111111111111111111111111111")
        self.assertEqual(context["sourceTxId"], "solana-tx-1")
        self.assertEqual(context["sourceTxHash"], "solana-tx-1")
