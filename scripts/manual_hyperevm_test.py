#!/usr/bin/env python3
from __future__ import annotations

import argparse
import json
import sys
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, Optional
from uuid import uuid4

ROOT = Path(__file__).resolve().parent.parent
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from web3 import Web3

from app.main import config, escrow_manager, hyperevm_service, intent_service, store
from app.models import GaslessIntent, IntentEvent

ERC20_METADATA_ABI = [
    {
        "type": "function",
        "name": "decimals",
        "stateMutability": "view",
        "inputs": [],
        "outputs": [{"name": "", "type": "uint8"}],
    },
    {
        "type": "function",
        "name": "symbol",
        "stateMutability": "view",
        "inputs": [],
        "outputs": [{"name": "", "type": "string"}],
    },
]


def now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Manual HyperEVM funding test that bypasses LI.FI and executes account creation plus deposit."
    )
    parser.add_argument("--raw", action="store_true", help="Print raw JSON only.")
    subparsers = parser.add_subparsers(dest="command", required=True)

    subparsers.add_parser(
        "show-escrow",
        help="Print the escrow address and collateral token you should fund on HyperEVM.",
    )

    run = subparsers.add_parser(
        "run",
        help="Create a manual intent and execute account creation plus deposit from the funded escrow wallet.",
    )
    run.add_argument("--user-address", required=True, help="The user EOA that will own the created account.")
    run.add_argument(
        "--received-amount",
        required=True,
        help="Collateral amount in token smallest units. Must already be present in escrow.",
    )
    run.add_argument(
        "--sub-account-id",
        help="Optional sub-account ID. If omitted, a unique manual ID is generated.",
    )
    run.add_argument(
        "--sub-account-name",
        help="Optional sub-account name. Default: gasless-<sub-account-id>.",
    )
    run.add_argument(
        "--source-tx-hash",
        default="0x" + "11" * 32,
        help="Dummy tx hash stored on the manual intent. Default: 0x1111...1111",
    )

    return parser.parse_args()


def print_json(data: Any) -> None:
    print(json.dumps(data, indent=2))


def build_manual_intent(
    *,
    user_address: str,
    received_amount: str,
    sub_account_id: str,
    sub_account_name: str,
    source_tx_hash: str,
) -> GaslessIntent:
    store.initialize()
    existing = store.list()
    escrow_address, escrow_wallet_index = escrow_manager.assign_address(existing)
    timestamp = now_iso()

    return GaslessIntent(
        id=str(uuid4()),
        ownerAccountAddress=Web3.to_checksum_address(user_address),
        sourceWalletType="evm",
        sourceWalletAddress=Web3.to_checksum_address(user_address),
        sourceChainId=config.hyperevm_chain_id,
        sourceTokenAddress=config.contract_config["collateralToken"],
        destinationTokenAddress=config.contract_config["collateralToken"],
        fromAmount=received_amount,
        quotedDestinationAmount=received_amount,
        minimumAmount=str(config.min_collateral_amount),
        subAccountId=sub_account_id,
        subAccountName=sub_account_name,
        escrowStrategy="shared" if escrow_wallet_index is None else "perUser",
        escrowAddress=escrow_address,
        escrowWalletIndex=escrow_wallet_index,
        status="FUNDS_RECEIVED",
        quote={"manualTest": True},
        quoteBridgeTool=None,
        createdAt=timestamp,
        updatedAt=timestamp,
        history=[
            IntentEvent(
                at=timestamp,
                status="FUNDS_RECEIVED",
                message="Manual HyperEVM funding test created without LI.FI",
            )
        ],
        sourceTxId=source_tx_hash,
        bridgeStatus="MANUAL_TEST",
        receivedAmount=received_amount,
    )


def get_escrow_info() -> Dict[str, Any]:
    store.initialize()
    escrow_address, escrow_wallet_index = escrow_manager.assign_address(store.list())
    collateral_token = config.contract_config["collateralToken"]
    token_contract = hyperevm_service.w3.eth.contract(
        address=Web3.to_checksum_address(collateral_token),
        abi=ERC20_METADATA_ABI,
    )
    token_balance = hyperevm_service.get_token_balance(collateral_token, escrow_address)
    native_balance = hyperevm_service.w3.eth.get_balance(Web3.to_checksum_address(escrow_address))
    token_decimals = int(token_contract.functions.decimals().call())
    token_symbol = token_contract.functions.symbol().call()
    return {
        "escrowAddress": escrow_address,
        "escrowStrategy": config.escrow_strategy,
        "escrowWalletIndex": escrow_wallet_index,
        "collateralToken": collateral_token,
        "collateralTokenSymbol": token_symbol,
        "collateralTokenDecimals": token_decimals,
        "minCollateralAmount": str(config.min_collateral_amount),
        "minGasBalanceWei": str(config.min_gas_balance_wei),
        "currentCollateralBalance": str(token_balance),
        "currentNativeBalanceWei": str(native_balance),
        "hyperevmChainId": config.hyperevm_chain_id,
    }


def print_escrow_info(info: Dict[str, Any]) -> None:
    print(f"escrow_address: {info['escrowAddress']}")
    print(f"escrow_strategy: {info['escrowStrategy']}")
    if info["escrowWalletIndex"] is not None:
        print(f"escrow_wallet_index: {info['escrowWalletIndex']}")
    print(f"hyperevm_chain_id: {info['hyperevmChainId']}")
    print(f"collateral_token: {info['collateralToken']}")
    print(f"collateral_token_symbol: {info['collateralTokenSymbol']}")
    print(f"collateral_token_decimals: {info['collateralTokenDecimals']}")
    print(f"min_collateral_amount: {info['minCollateralAmount']}")
    print(f"min_gas_balance_wei: {info['minGasBalanceWei']}")
    print(f"current_collateral_balance: {info['currentCollateralBalance']}")
    print(f"current_native_balance_wei: {info['currentNativeBalanceWei']}")


def validate_inputs(*, user_address: str, received_amount: str, source_tx_hash: str) -> None:
    if not Web3.is_address(user_address):
        raise RuntimeError("user-address must be a valid EVM address")
    try:
        amount = int(received_amount)
    except ValueError as exc:
        raise RuntimeError("received-amount must be a valid integer string") from exc
    if amount <= 0:
        raise RuntimeError("received-amount must be greater than zero")
    if not source_tx_hash.startswith("0x") or len(source_tx_hash) != 66:
        raise RuntimeError("source-tx-hash must be a 32-byte hex string")


def main() -> int:
    args = parse_args()

    try:
        if args.command == "show-escrow":
            info = get_escrow_info()
            if args.raw:
                print_json(info)
            else:
                print_escrow_info(info)
            return 0

        if args.command == "run":
            validate_inputs(
                user_address=args.user_address,
                received_amount=args.received_amount,
                source_tx_hash=args.source_tx_hash,
            )
            sub_account_id = args.sub_account_id or f"manual-{uuid4().hex[:8]}"
            sub_account_name = args.sub_account_name or f"gasless-{sub_account_id}"
            info = get_escrow_info()
            if int(args.received_amount) < config.min_collateral_amount:
                raise RuntimeError(
                    f"received-amount {args.received_amount} is below configured minimum {config.min_collateral_amount}"
                )
            if int(info["currentCollateralBalance"]) < int(args.received_amount):
                raise RuntimeError(
                    f"Escrow collateral balance {info['currentCollateralBalance']} is lower than requested amount {args.received_amount}"
                )

            intent = build_manual_intent(
                user_address=args.user_address,
                received_amount=args.received_amount,
                sub_account_id=sub_account_id,
                sub_account_name=sub_account_name,
                source_tx_hash=args.source_tx_hash,
            )
            store.create(intent)
            intent_service._execute_on_chain(intent.id)
            final_intent = intent_service.get_intent(intent.id).model_dump()

            if args.raw:
                print_json(final_intent)
            else:
                print(f"intent_id: {final_intent['id']}")
                print(f"sub_account_id: {final_intent['subAccountId']}")
                print(f"sub_account_name: {final_intent.get('subAccountName')}")
                print(f"escrow_address: {final_intent['escrowAddress']}")
                print(f"received_amount: {final_intent['receivedAmount']}")
                print(f"status: {final_intent['status']}")
                print(f"created_account_address: {final_intent.get('createdAccountAddress')}")
                print(f"create_account_tx_hash: {final_intent.get('createAccountTxHash')}")
                if final_intent.get("approvalTxHash"):
                    print(f"approval_tx_hash: {final_intent['approvalTxHash']}")
                print(f"deposit_tx_hash: {final_intent.get('depositTxHash')}")
            return 0

        raise RuntimeError(f"Unsupported command: {args.command}")
    except Exception as exc:
        print(f"Error: {exc}")
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
