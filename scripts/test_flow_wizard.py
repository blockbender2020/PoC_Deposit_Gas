#!/usr/bin/env python3
from __future__ import annotations

import argparse
import json
import sys
import time
from pathlib import Path
from typing import Any, Dict, Optional

from frontend_cli import (
    DEFAULT_BASE_URL,
    DEFAULT_STATE_FILE,
    DEFAULT_USER_AGENT,
    FINAL_STATUSES,
    ApiClient,
    default_sub_account_name,
    load_state,
    print_intent_summary,
    save_state,
)


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Interactive frontend test wizard for either direct HyperEVM funding or LI.FI bridge flow."
    )
    parser.add_argument(
        "--base-url",
        default=DEFAULT_BASE_URL,
        help=f"Backend base URL. Default: {DEFAULT_BASE_URL}",
    )
    parser.add_argument(
        "--state-file",
        type=Path,
        default=DEFAULT_STATE_FILE,
        help=f"Local file used to remember the last intent ID. Default: {DEFAULT_STATE_FILE}",
    )
    parser.add_argument(
        "--user-agent",
        default=DEFAULT_USER_AGENT,
        help="HTTP User-Agent header for backend requests.",
    )
    parser.add_argument(
        "--interval",
        type=float,
        default=5.0,
        help="Poll interval in seconds. Default: 5",
    )
    parser.add_argument(
        "--timeout",
        type=float,
        default=300.0,
        help="Polling timeout in seconds. Default: 300",
    )
    parser.add_argument(
        "--raw",
        action="store_true",
        help="Print raw JSON responses.",
    )
    return parser.parse_args()


def ask(prompt: str, *, default: Optional[str] = None, allow_empty: bool = False) -> str:
    suffix = f" [{default}]" if default is not None else ""
    while True:
        value = input(f"{prompt}{suffix}: ").strip()
        if value:
            return value
        if default is not None:
            return default
        if allow_empty:
            return ""
        print("This value is required.")


def ask_chain() -> str:
    while True:
        value = ask("Which chain are you working on? Enter `HyperEVM` or `Other`")
        normalized = value.strip().lower()
        if normalized in {"hyperevm", "hyper", "999"}:
            return "HyperEVM"
        if normalized in {"other", "bridge", "lifi"}:
            return "Other"
        print("Please answer `HyperEVM` or `Other`.")


def print_json(data: Any) -> None:
    print(json.dumps(data, indent=2))


def print_health(health: Dict[str, Any]) -> None:
    print()
    print("backend:")
    print(f"  chain_id: {health['chainId']}")
    print(f"  escrow_strategy: {health['escrowStrategy']}")
    print(f"  min_collateral_amount: {health['minCollateralAmount']}")


def store_intent(state_file: Path, state: Dict[str, Any], base_url: str, intent: Dict[str, Any]) -> None:
    state["last_intent_id"] = intent["id"]
    state["last_base_url"] = base_url
    save_state(state_file, state)


def poll_intent(client: ApiClient, *, intent_id: str, interval: float, timeout: float, raw: bool) -> int:
    deadline = time.monotonic() + timeout
    while True:
        intent = client.get(f"/v1/intents/{intent_id}")
        if raw:
            print_json(intent)
        else:
            print()
            print_intent_summary(intent)

        if intent["status"] in FINAL_STATUSES:
            return 0 if intent["status"] == "COMPLETED" else 1

        if time.monotonic() >= deadline:
            raise RuntimeError(f"Polling timed out after {timeout} seconds while waiting for intent {intent_id}")

        time.sleep(interval)


def run_direct_flow(
    client: ApiClient,
    *,
    base_url: str,
    state_file: Path,
    state: Dict[str, Any],
    health: Dict[str, Any],
    interval: float,
    timeout: float,
    raw: bool,
) -> int:
    print()
    print("direct HyperEVM flow selected")
    user_address = ask("User wallet address")
    amount = ask(
        "Funding amount in collateral token smallest units",
        default=health["minCollateralAmount"],
    )
    sub_account_id = ask("Sub-account ID", default="0")
    sub_account_name = ask("Sub-account name", default=default_sub_account_name(sub_account_id))
    create_account = ask("Create a new account? Enter `yes` or `no`", default="yes").strip().lower() in {"yes", "y"}
    target_account_address = None if create_account else ask("Existing subaccount address")

    payload: Dict[str, Any] = {
        "userAddress": user_address,
        "amount": amount,
        "subAccountId": sub_account_id,
        "subAccountName": sub_account_name,
        "createAccount": create_account,
    }
    if target_account_address:
        payload["targetAccountAddress"] = target_account_address
    intent = client.post("/v1/direct-intents", payload)
    store_intent(state_file, state, base_url, intent)

    print()
    print("direct intent created")
    if raw:
        print_json(intent)
    else:
        print_intent_summary(intent)

    print()
    print("what to do now:")
    print(f"1. On HyperEVM, send token {intent['destinationTokenAddress']} from your wallet to escrow {intent['escrowAddress']}.")
    print(f"2. Send at least {intent['fromAmount']} base units.")
    if intent.get("createAccount") is False:
        print(f"3. The backend will skip account creation and deposit into existing subaccount {intent['targetAccountAddress']}.")
    print("4. Wait until the transfer tx is mined.")
    tx_hash = ask("Paste the mined HyperEVM tx hash")

    result = client.post(f"/v1/direct-intents/{intent['id']}/tx", {"txHash": tx_hash})
    print()
    print("funding tx registered")
    if raw:
        print_json(result)
    else:
        print_intent_summary(result)

    print()
    print("polling until the backend finishes...")
    return poll_intent(client, intent_id=intent["id"], interval=interval, timeout=timeout, raw=raw)


def run_bridge_flow(
    client: ApiClient,
    *,
    base_url: str,
    state_file: Path,
    state: Dict[str, Any],
    interval: float,
    timeout: float,
    raw: bool,
) -> int:
    print()
    print("LI.FI bridge flow selected")
    user_address = ask("User wallet address")
    source_chain_id = ask("Source chain ID, for example 42161 for Arbitrum")
    source_token_address = ask("Source token address")
    from_amount = ask("Source token amount in smallest units")
    from_amount_for_gas = ask("Optional amount for gas in smallest units", allow_empty=True)
    sub_account_id = ask("Sub-account ID", default="0")
    sub_account_name = ask("Sub-account name", default=default_sub_account_name(sub_account_id))
    create_account = ask("Create a new account? Enter `yes` or `no`", default="yes").strip().lower() in {"yes", "y"}
    target_account_address = None if create_account else ask("Existing subaccount address")
    slippage = ask("Slippage", default="0.005")

    payload: Dict[str, Any] = {
        "userAddress": user_address,
        "sourceChainId": int(source_chain_id),
        "sourceTokenAddress": source_token_address,
        "fromAmount": from_amount,
        "subAccountId": sub_account_id,
        "subAccountName": sub_account_name,
        "createAccount": create_account,
        "slippage": float(slippage),
    }
    if target_account_address:
        payload["targetAccountAddress"] = target_account_address
    if from_amount_for_gas:
        payload["fromAmountForGas"] = from_amount_for_gas

    intent = client.post("/v1/intents", payload)
    store_intent(state_file, state, base_url, intent)

    print()
    print("bridge intent created")
    if raw:
        print_json(intent)
    else:
        print_intent_summary(intent)

    quote = intent.get("quote") or {}
    print()
    print("what to do now:")
    print("1. Execute the returned LI.FI quote from your wallet.")
    print(f"2. The destination escrow is {intent['escrowAddress']}.")
    if intent.get("createAccount") is False:
        print(f"3. The backend will skip account creation and deposit into existing subaccount {intent['targetAccountAddress']}.")
        print("4. Wait until the source-chain tx is mined.")
    else:
        print("3. Wait until the source-chain tx is mined.")
    print()
    print("quote snippet:")
    print_json(
        {
            "tool": quote.get("tool"),
            "action": quote.get("action"),
            "estimate": quote.get("estimate"),
            "transactionRequest": quote.get("transactionRequest"),
        }
    )
    tx_hash = ask("Paste the mined source-chain tx hash")

    result = client.post(f"/v1/intents/{intent['id']}/source-tx", {"txHash": tx_hash})
    print()
    print("source tx registered")
    if raw:
        print_json(result)
    else:
        print_intent_summary(result)

    print()
    print("polling until the backend finishes...")
    return poll_intent(client, intent_id=intent["id"], interval=interval, timeout=timeout, raw=raw)


def main() -> int:
    args = parse_args()
    state = load_state(args.state_file)
    chain = ask_chain()
    base_url = ask("Backend base URL", default=args.base_url)
    client = ApiClient(base_url, user_agent=args.user_agent)

    try:
        health = client.get("/health")
        print_health(health)

        if chain == "HyperEVM":
            return run_direct_flow(
                client,
                base_url=base_url,
                state_file=args.state_file,
                state=state,
                health=health,
                interval=args.interval,
                timeout=args.timeout,
                raw=args.raw,
            )

        return run_bridge_flow(
            client,
            base_url=base_url,
            state_file=args.state_file,
            state=state,
            interval=args.interval,
            timeout=args.timeout,
            raw=args.raw,
        )
    except KeyboardInterrupt:
        print("\nInterrupted", file=sys.stderr)
        return 130
    except Exception as exc:
        print(f"Error: {exc}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
