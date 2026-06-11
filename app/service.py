from __future__ import annotations

import re
from datetime import datetime, timezone
from threading import Lock
from typing import Any, Dict, Optional
from uuid import uuid4

from web3 import Web3

from app.errors import HttpError
from app.escrow import EscrowManager
from app.hyperevm import HyperEvmService
from app.lifi import LiFiClient
from app.models import (
    CreateDirectIntentRequest,
    CreateIntentRequest,
    GaslessIntent,
    IntentEvent,
    IntentStatus,
    SubmitDirectFundingTxRequest,
    SubmitSourceTxRequest,
    WalletType,
)
from app.store import IntentStore


EVM_TX_HASH_RE = re.compile(r"^0x[a-fA-F0-9]{64}$")


def _now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


def _extract_quoted_destination_amount(quote: Dict[str, Any]) -> Optional[str]:
    estimate = quote.get("estimate") or {}
    return estimate.get("toAmountMin") or estimate.get("toAmount") or quote.get("toAmountMin") or quote.get("toAmount")


def _extract_bridge_tool(quote: Dict[str, Any]) -> Optional[str]:
    return quote.get("tool")


def _is_done_status(status: Optional[str]) -> bool:
    return (status or "").upper() in {"DONE", "COMPLETED", "SUCCESS"}


def _is_pending_status(status: Optional[str]) -> bool:
    return (status or "").upper() in {
        "PENDING",
        "WAIT_SOURCE_CONFIRMATIONS",
        "WAIT_DESTINATION_TRANSACTION",
        "UNKNOWN",
    }


class IntentService:
    def __init__(
        self,
        *,
        store: IntentStore,
        lifi_client: LiFiClient,
        hyperevm_service: HyperEvmService,
        escrow_manager: EscrowManager,
        min_collateral_amount: int,
        destination_token_address: str,
        destination_chain_id: int,
        contract_config: Dict[str, Any],
    ) -> None:
        self.store = store
        self.lifi_client = lifi_client
        self.hyperevm_service = hyperevm_service
        self.escrow_manager = escrow_manager
        self.min_collateral_amount = min_collateral_amount
        self.destination_token_address = destination_token_address
        self.destination_chain_id = destination_chain_id
        self.contract_config = contract_config
        self._processing = set()
        self._processing_lock = Lock()

    def list_intents(self) -> list[GaslessIntent]:
        return self.store.list()

    def get_intent(self, intent_id: str) -> GaslessIntent:
        intent = self.store.get(intent_id)
        if not intent:
            raise HttpError(404, f"Intent {intent_id} not found")
        return intent

    def create_intent(self, payload: CreateIntentRequest) -> GaslessIntent:
        owner_account_address = self._validate_owner_account_address(payload.ownerAccountAddress)
        if payload.sourceWalletType == "solana" and not payload.sourceWalletAddress:
            raise HttpError(400, "sourceWalletAddress is required when sourceWalletType is solana")
        source_wallet_address = self._validate_source_wallet_address(
            payload.sourceWalletAddress or payload.ownerAccountAddress,
            payload.sourceWalletType,
        )
        source_token_address = self._validate_source_token_address(
            payload.sourceTokenAddress,
            payload.sourceWalletType,
        )
        target_account_address = self._validate_account_target(
            create_account=payload.createAccount,
            target_account_address=payload.targetAccountAddress,
        )

        self._parse_int(payload.fromAmount, "fromAmount")
        if payload.fromAmountForGas is not None:
            self._parse_int(payload.fromAmountForGas, "fromAmountForGas")
        sub_account_name = self._resolve_sub_account_name(
            sub_account_id=payload.subAccountId,
            requested_name=payload.subAccountName,
        )

        existing = self.store.list()
        escrow_address, escrow_wallet_index = self.escrow_manager.assign_address(existing)
        request = payload.model_dump()
        request["ownerAccountAddress"] = owner_account_address
        request["sourceWalletAddress"] = source_wallet_address
        request["sourceTokenAddress"] = source_token_address

        quote = self.lifi_client.get_quote(request, escrow_address)
        quoted_destination_amount = _extract_quoted_destination_amount(quote)
        if not quoted_destination_amount:
            raise HttpError(502, "LI.FI quote did not include a destination amount estimate")
        if int(quoted_destination_amount) < self.min_collateral_amount:
            raise HttpError(
                400,
                f"Quoted destination amount {quoted_destination_amount} is below the minimum collateral {self.min_collateral_amount}",
            )

        created_at = _now_iso()
        status: IntentStatus = "AWAITING_BRIDGE"
        intent = GaslessIntent(
            id=str(uuid4()),
            flowType="bridge",
            ownerAccountAddress=owner_account_address,
            createAccount=payload.createAccount,
            targetAccountAddress=target_account_address,
            sourceWalletType=payload.sourceWalletType,
            sourceWalletAddress=source_wallet_address,
            sourceChainId=payload.sourceChainId,
            sourceTokenAddress=source_token_address,
            destinationTokenAddress=self.destination_token_address,
            fromAmount=payload.fromAmount,
            quotedDestinationAmount=quoted_destination_amount,
            minimumAmount=str(self.min_collateral_amount),
            subAccountId=payload.subAccountId,
            subAccountName=sub_account_name,
            escrowStrategy="shared" if escrow_wallet_index is None else "perUser",
            escrowAddress=escrow_address,
            escrowWalletIndex=escrow_wallet_index,
            status=status,
            quote=quote,
            quoteBridgeTool=_extract_bridge_tool(quote),
            createdAt=created_at,
            updatedAt=created_at,
            history=[
                IntentEvent(
                    at=created_at,
                    status=status,
                    message="Intent created and LI.FI quote generated",
                )
            ],
        )
        self.store.create(intent)
        return intent

    def create_direct_intent(self, payload: CreateDirectIntentRequest) -> GaslessIntent:
        owner_account_address = self._validate_owner_account_address(payload.ownerAccountAddress)
        target_account_address = self._validate_account_target(
            create_account=payload.createAccount,
            target_account_address=payload.targetAccountAddress,
        )

        amount = self._parse_int(payload.amount, "amount")
        if amount < self.min_collateral_amount:
            raise HttpError(
                400,
                f"Amount {payload.amount} is below the minimum collateral {self.min_collateral_amount}",
            )
        sub_account_name = self._resolve_sub_account_name(
            sub_account_id=payload.subAccountId,
            requested_name=payload.subAccountName,
        )

        existing = self.store.list()
        escrow_address, escrow_wallet_index = self.escrow_manager.assign_address(existing)
        created_at = _now_iso()
        status: IntentStatus = "AWAITING_FUNDS"
        token_address = self.destination_token_address

        intent = GaslessIntent(
            id=str(uuid4()),
            flowType="direct",
            ownerAccountAddress=owner_account_address,
            createAccount=payload.createAccount,
            targetAccountAddress=target_account_address,
            sourceWalletType="evm",
            sourceWalletAddress=owner_account_address,
            sourceChainId=self.destination_chain_id,
            sourceTokenAddress=token_address,
            destinationTokenAddress=token_address,
            fromAmount=payload.amount,
            quotedDestinationAmount=payload.amount,
            minimumAmount=str(self.min_collateral_amount),
            subAccountId=payload.subAccountId,
            subAccountName=sub_account_name,
            escrowStrategy="shared" if escrow_wallet_index is None else "perUser",
            escrowAddress=escrow_address,
            escrowWalletIndex=escrow_wallet_index,
            status=status,
            quote={"mode": "direct", "chainId": self.destination_chain_id, "token": token_address},
            createdAt=created_at,
            updatedAt=created_at,
            history=[
                IntentEvent(
                    at=created_at,
                    status=status,
                    message="Direct HyperEVM funding intent created",
                )
            ],
        )
        self.store.create(intent)
        return intent

    def submit_source_tx(self, intent_id: str, payload: SubmitSourceTxRequest) -> GaslessIntent:
        intent = self.get_intent(intent_id)
        if intent.flowType != "bridge":
            raise HttpError(400, f"Intent {intent_id} is not a bridge intent")
        source_tx_id = self._validate_source_tx_id(payload.sourceTxId, intent.sourceWalletType)

        for existing in self.store.list():
            if existing.id != intent_id and existing.sourceTxId == source_tx_id:
                raise HttpError(409, f"Source transaction id is already registered on intent {existing.id}")

        updated = self.store.update(
            intent_id,
            lambda intent: self._with_event(
                intent.model_copy(
                    update={
                        "sourceTxId": source_tx_id,
                        "bridgeStatus": "SUBMITTED",
                        "status": "BRIDGE_SUBMITTED",
                    }
                ),
                "BRIDGE_SUBMITTED",
                "Frontend registered source-chain bridge transaction id",
            ),
        )

        if updated is None:
            raise HttpError(404, f"Intent {intent_id} not found")

        self.process_intent(intent_id)
        return self.get_intent(intent_id)

    def submit_direct_funding_tx(self, intent_id: str, payload: SubmitDirectFundingTxRequest) -> GaslessIntent:
        if not EVM_TX_HASH_RE.match(payload.txHash):
            raise HttpError(400, "txHash must be a valid 32-byte transaction hash")

        intent = self.get_intent(intent_id)
        if intent.flowType != "direct":
            raise HttpError(400, f"Intent {intent_id} is not a direct funding intent")

        for existing in self.store.list():
            if existing.id != intent_id and existing.sourceTxId == payload.txHash:
                raise HttpError(409, f"Funding tx hash is already registered on intent {existing.id}")

        updated = self.store.update(
            intent_id,
            lambda current: self._with_event(
                current.model_copy(
                    update={
                        "sourceTxId": payload.txHash,
                        "bridgeStatus": "DIRECT_SUBMITTED",
                        "status": "FUNDING_SUBMITTED",
                    }
                ),
                "FUNDING_SUBMITTED",
                "Frontend registered direct HyperEVM funding transaction",
            ),
        )

        if updated is None:
            raise HttpError(404, f"Intent {intent_id} not found")

        self.process_intent(intent_id)
        return self.get_intent(intent_id)

    def process_pending(self) -> None:
        for intent in self.store.list():
            if intent.status in {
                "BRIDGE_SUBMITTED",
                "BRIDGE_PENDING",
                "FUNDING_SUBMITTED",
                "FUNDING_PENDING",
                "FUNDS_RECEIVED",
                "EXECUTING",
            }:
                try:
                    self.process_intent(intent.id)
                except Exception:
                    continue

    def process_intent(self, intent_id: str) -> None:
        with self._processing_lock:
            if intent_id in self._processing:
                return
            self._processing.add(intent_id)

        try:
            intent = self.get_intent(intent_id)
            if intent.status in {"COMPLETED", "FAILED"} or not intent.sourceTxId:
                return

            if intent.flowType == "direct":
                self._process_direct_intent(intent)
                return

            try:
                status = self.lifi_client.get_status(
                    tx_hash=intent.sourceTxId,
                    from_chain_id=intent.sourceChainId,
                    to_chain_id=self.destination_chain_id,
                    bridge=intent.quoteBridgeTool,
                )
            except HttpError as exc:
                if exc.status_code == 404:
                    self.store.update(
                        intent_id,
                        lambda current: self._with_event(
                            current.model_copy(
                                update={
                                    "status": "BRIDGE_PENDING",
                                    "bridgeStatus": "NOT_INDEXED_YET",
                                }
                            ),
                            "BRIDGE_PENDING",
                            "Bridge transaction is not indexed by LI.FI yet",
                        ),
                    )
                    return
                raise

            if _is_pending_status(status.get("status")):
                self.store.update(
                    intent_id,
                    lambda current: self._with_event(
                        current.model_copy(
                            update={
                                "status": "BRIDGE_PENDING",
                                "bridgeStatus": status.get("status") or "PENDING",
                            }
                        ),
                        "BRIDGE_PENDING",
                        f"Bridge still pending in LI.FI ({status.get('status') or 'PENDING'})",
                    ),
                )
                return

            if not _is_done_status(status.get("status")):
                raise RuntimeError(f"LI.FI status is not executable yet: {status.get('status') or 'UNKNOWN'}")

            receiving = status.get("receiving") or {}
            received_amount = receiving.get("amount")
            if not received_amount:
                raise RuntimeError("LI.FI status completed without a receiving amount")
            if int(received_amount) < self.min_collateral_amount:
                raise RuntimeError(
                    f"Received amount {received_amount} is below minimum collateral {self.min_collateral_amount}"
                )

            receiving_chain_id = receiving.get("chainId")
            if receiving_chain_id is not None and int(receiving_chain_id) != self.destination_chain_id:
                raise RuntimeError(f"Bridge completed on unexpected chain {receiving_chain_id}")

            receiving_address = receiving.get("walletAddress") or receiving.get("address")
            if receiving_address and Web3.to_checksum_address(receiving_address) != intent.escrowAddress:
                raise RuntimeError(f"Bridge completed to unexpected escrow {receiving_address}")

            self.store.update(
                intent_id,
                lambda current: self._with_event(
                    current.model_copy(
                        update={
                            "status": "FUNDS_RECEIVED",
                            "bridgeStatus": status.get("status") or "DONE",
                            "receivedAmount": str(received_amount),
                            "destinationTxHash": receiving.get("txHash"),
                        }
                    ),
                    "FUNDS_RECEIVED",
                    "Bridge funds received on HyperEVM",
                ),
            )
            self._execute_on_chain(intent_id)
        except Exception as exc:
            self._fail_intent(intent_id, exc)
        finally:
            with self._processing_lock:
                self._processing.discard(intent_id)

    def _process_direct_intent(self, intent: GaslessIntent) -> None:
        if not intent.sourceTxId:
            raise RuntimeError("Intent is missing funding tx hash")

        receipt = self.hyperevm_service.get_transaction_receipt(intent.sourceTxId)
        if not receipt:
            self.store.update(
                intent.id,
                lambda current: self._with_event(
                    current.model_copy(
                        update={
                            "status": "FUNDING_PENDING",
                            "bridgeStatus": "NOT_MINED_YET",
                        }
                    ),
                    "FUNDING_PENDING",
                    "Direct HyperEVM funding transaction is not mined yet",
                ),
            )
            return

        if int(receipt.get("status", 0)) != 1:
            raise RuntimeError(f"Direct funding transaction failed: {intent.sourceTxId}")

        tx = self.hyperevm_service.get_transaction(intent.sourceTxId)
        if not tx:
            raise RuntimeError(f"Could not load direct funding transaction {intent.sourceTxId}")

        tx_from = tx.get("from")
        if not tx_from or Web3.to_checksum_address(tx_from) != intent.sourceWalletAddress:
            raise RuntimeError(
                f"Funding transaction sender does not match intent source wallet {intent.sourceWalletAddress}"
            )

        received_amount = self.hyperevm_service.get_matching_transfer_amount(
            receipt=receipt,
            token=intent.destinationTokenAddress,
            from_address=intent.sourceWalletAddress,
            to_address=intent.escrowAddress,
        )
        if received_amount <= 0:
            raise RuntimeError(
                f"Funding transaction did not transfer {intent.destinationTokenAddress} from {intent.sourceWalletAddress} to escrow {intent.escrowAddress}"
            )

        minimum_required = max(int(intent.minimumAmount), int(intent.fromAmount))
        if received_amount < minimum_required:
            raise RuntimeError(
                f"Received amount {received_amount} is below required amount {minimum_required}"
            )

        self.store.update(
            intent.id,
            lambda current: self._with_event(
                current.model_copy(
                    update={
                        "status": "FUNDS_RECEIVED",
                        "bridgeStatus": "DIRECT_CONFIRMED",
                        "receivedAmount": str(received_amount),
                        "destinationTxHash": intent.sourceTxId,
                    }
                ),
                "FUNDS_RECEIVED",
                "Direct HyperEVM funding received in escrow",
            ),
        )
        self._execute_on_chain(intent.id)

    def _execute_on_chain(self, intent_id: str) -> None:
        intent = self.get_intent(intent_id)
        if not intent.sourceTxId or not intent.receivedAmount:
            raise RuntimeError("Intent is missing sourceTxId or receivedAmount")

        account = self.escrow_manager.get_execution_account(intent)
        self.hyperevm_service.ensure_gas_balance(account.address)

        balance = self.hyperevm_service.get_token_balance(intent.destinationTokenAddress, account.address)
        if balance < int(intent.receivedAmount):
            raise RuntimeError(
                f"Escrow balance {balance} is lower than received amount {intent.receivedAmount}"
            )

        self.store.update(
            intent_id,
            lambda current: self._with_event(
                current.model_copy(update={"status": "EXECUTING"}),
                "EXECUTING",
                "Starting HyperEVM account creation and deposit",
            ),
        )

        context = self._build_execution_context(intent)

        signer_context = self.contract_config.get("signerContext")
        if signer_context:
            self.hyperevm_service.execute_template(
                account=account,
                template=signer_context["set"],
                context=context,
            )

        try:
            create_result = None
            if intent.createAccount:
                if not intent.createAccountTxHash:
                    create_result = self.hyperevm_service.execute_template(
                        account=account,
                        template=self.contract_config["createAccount"],
                        context=context,
                    )
                    context.update(create_result["captured"])
                    updates = {
                        "createAccountTxHash": create_result["tx_hash"],
                        "updatedAt": _now_iso(),
                    }
                    if "createdAccountAddress" in create_result["captured"]:
                        updates["createdAccountAddress"] = Web3.to_checksum_address(create_result["captured"]["createdAccountAddress"])

                    self.store.update(
                        intent_id,
                        lambda current: current.model_copy(update=updates),
                    )
                    intent = self.get_intent(intent_id)
                    context = self._build_execution_context(intent)
                else:
                    context = self._build_execution_context(intent)
            else:
                context = self._build_execution_context(intent)

            approval_tx_hash = intent.approvalTxHash
            approval_cfg = self.contract_config.get("approval")
            if approval_cfg and not approval_tx_hash:
                approval_tx_hash = self.hyperevm_service.ensure_approval(
                    account=account,
                    token=intent.destinationTokenAddress,
                    amount=int(intent.receivedAmount),
                    approval=approval_cfg,
                )
                if approval_tx_hash:
                    self.store.update(
                        intent_id,
                        lambda current: current.model_copy(
                            update={"approvalTxHash": approval_tx_hash, "updatedAt": _now_iso()}
                        ),
                    )
                    intent = self.get_intent(intent_id)

            deposit_tx_hash = intent.depositTxHash
            if not deposit_tx_hash:
                deposit_result = self.hyperevm_service.execute_template(
                    account=account,
                    template=self.contract_config["deposit"],
                    context=self._build_execution_context(self.get_intent(intent_id)),
                )
                deposit_tx_hash = deposit_result["tx_hash"]

            final_intent = self.get_intent(intent_id)
            completion_message = (
                "Account created and collateral deposited"
                if final_intent.createAccount
                else "Collateral deposited into existing subaccount"
            )
            self.store.update(
                intent_id,
                lambda current: self._with_event(
                    current.model_copy(
                        update={
                            "createAccountTxHash": final_intent.createAccountTxHash or (create_result["tx_hash"] if create_result else intent.createAccountTxHash),
                            "approvalTxHash": approval_tx_hash,
                            "depositTxHash": deposit_tx_hash,
                            "status": "COMPLETED",
                        }
                    ),
                    "COMPLETED",
                    completion_message,
                ),
            )
        finally:
            if signer_context:
                clear_context = dict(context)
                self.hyperevm_service.execute_template(
                    account=account,
                    template=signer_context["clear"],
                    context=clear_context,
                )

    def _fail_intent(self, intent_id: str, exc: Exception) -> None:
        self.store.update(
            intent_id,
            lambda intent: self._with_event(
                intent.model_copy(update={"status": "FAILED", "failureReason": str(exc)}),
                "FAILED",
                str(exc),
            ),
        )

    def _with_event(self, intent: GaslessIntent, status: IntentStatus, message: str) -> GaslessIntent:
        timestamp = _now_iso()
        return intent.model_copy(
            update={
                "status": status,
                "updatedAt": timestamp,
                "history": [*intent.history, IntentEvent(at=timestamp, status=status, message=message)],
            }
        )

    @staticmethod
    def _parse_int(value: str, field_name: str) -> int:
        try:
            return int(value)
        except ValueError as exc:
            raise HttpError(400, f"{field_name} must be a valid integer string") from exc

    def _build_execution_context(self, intent: GaslessIntent) -> Dict[str, Any]:
        static_context = dict(self.contract_config.get("context", {}))
        effective_account_address = intent.createdAccountAddress or intent.targetAccountAddress
        context = {
            "userAddress": intent.ownerAccountAddress,
            "ownerAccountAddress": intent.ownerAccountAddress,
            "sourceWalletType": intent.sourceWalletType,
            "sourceWalletAddress": intent.sourceWalletAddress,
            "escrowAddress": intent.escrowAddress,
            "collateralToken": intent.destinationTokenAddress,
            "receivedAmount": int(intent.receivedAmount or "0"),
            "minimumAmount": int(intent.minimumAmount),
            "subAccountId": intent.subAccountId,
            "subAccountName": self._resolve_sub_account_name(
                sub_account_id=intent.subAccountId,
                requested_name=intent.subAccountName,
            ),
            "sourceChainId": intent.sourceChainId,
            "sourceTxId": intent.sourceTxId,
            "sourceTxHash": intent.sourceTxId,
            "createdAccountAddress": effective_account_address,
            **static_context,
        }
        return context

    def _resolve_sub_account_name(self, *, sub_account_id: str, requested_name: Optional[str]) -> str:
        if requested_name is not None:
            normalized = requested_name.strip()
            if not normalized:
                raise HttpError(400, "subAccountName must not be empty")
            return normalized

        static_context = dict(self.contract_config.get("context", {}))
        name_prefix = static_context.get("subAccountNamePrefix", "gasless-")
        return f"{name_prefix}{sub_account_id}"

    @staticmethod
    def _validate_owner_account_address(value: str) -> str:
        if not Web3.is_address(value):
            raise HttpError(400, "ownerAccountAddress must be a valid EVM address")
        return Web3.to_checksum_address(value)

    @staticmethod
    def _validate_source_wallet_address(value: str, wallet_type: WalletType) -> str:
        normalized = value.strip()
        if not normalized:
            raise HttpError(400, "sourceWalletAddress must not be empty")
        if wallet_type == "evm":
            if not Web3.is_address(normalized):
                raise HttpError(400, "sourceWalletAddress must be a valid EVM address")
            return Web3.to_checksum_address(normalized)
        return normalized

    @staticmethod
    def _validate_source_token_address(value: str, wallet_type: WalletType) -> str:
        normalized = value.strip()
        if not normalized:
            raise HttpError(400, "sourceTokenAddress must not be empty")
        if wallet_type == "evm":
            if not Web3.is_address(normalized):
                raise HttpError(400, "sourceTokenAddress must be a valid EVM address")
            return Web3.to_checksum_address(normalized)
        return normalized

    @staticmethod
    def _validate_source_tx_id(value: str, wallet_type: WalletType) -> str:
        normalized = value.strip()
        if not normalized:
            raise HttpError(400, "sourceTxId must not be empty")
        if wallet_type == "evm" and not EVM_TX_HASH_RE.match(normalized):
            raise HttpError(400, "sourceTxId must be a valid 32-byte transaction hash for EVM funding")
        return normalized

    @staticmethod
    def _validate_account_target(*, create_account: bool, target_account_address: Optional[str]) -> Optional[str]:
        if create_account:
            if target_account_address is None:
                return None
            if not Web3.is_address(target_account_address):
                raise HttpError(400, "targetAccountAddress must be a valid EVM address")
            return Web3.to_checksum_address(target_account_address)

        if not target_account_address:
            raise HttpError(400, "targetAccountAddress is required when createAccount is false")
        if not Web3.is_address(target_account_address):
            raise HttpError(400, "targetAccountAddress must be a valid EVM address")
        return Web3.to_checksum_address(target_account_address)
