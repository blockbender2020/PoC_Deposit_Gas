#!/usr/bin/env python3
from __future__ import annotations

import argparse
import json
import os
import sys
import time
from pathlib import Path
from typing import Any, Dict, Optional
from urllib.error import HTTPError, URLError
from urllib.request import Request, urlopen


DEFAULT_BASE_URL = "http://127.0.0.1:3000"
DEFAULT_STATE_FILE = Path("storage/frontend-client-state.json")
FINAL_STATUSES = {"COMPLETED", "FAILED"}
DEFAULT_USER_AGENT = os.environ.get(
    "FRONTEND_CLI_USER_AGENT",
    (
        "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) "
        "AppleWebKit/537.36 (KHTML, like Gecko) "
        "Chrome/137.0.0.0 Safari/537.36"
    ),
)


def default_sub_account_name(sub_account_id: str) -> str:
    return f"gasless-{sub_account_id}"


class ApiClient:
    def __init__(self, base_url: str, *, user_agent: Optional[str] = None) -> None:
        self.base_url = base_url.rstrip("/")
        self.user_agent = user_agent or DEFAULT_USER_AGENT

    def get(self, path: str) -> Any:
        return self._request("GET", path)

    def post(self, path: str, payload: Optional[Dict[str, Any]] = None) -> Any:
        return self._request("POST", path, payload or {})

    def _request(self, method: str, path: str, payload: Optional[Dict[str, Any]] = None) -> Any:
        data = None if payload is None else json.dumps(payload).encode()
        request = Request(
            f"{self.base_url}{path}",
            method=method,
            data=data,
            headers={
                "Accept": "application/json",
                "Content-Type": "application/json",
                "User-Agent": self.user_agent,
            },
        )

        try:
            with urlopen(request, timeout=30) as response:
                raw = response.read().decode()
                return json.loads(raw) if raw else {}
        except HTTPError as exc:
            body = exc.read().decode()
            try:
                parsed = json.loads(body) if body else {}
            except ValueError:
                parsed = body
            message = parsed.get("error") if isinstance(parsed, dict) else parsed
            raise RuntimeError(f"HTTP {exc.code}: {message}") from exc
        except URLError as exc:
            raise RuntimeError(f"Could not reach backend at {self.base_url}: {exc.reason}") from exc
        except ValueError as exc:
            raise RuntimeError("Server response was not valid JSON") from exc


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Small CLI client that acts like a minimal frontend for the gasless deposit backend."
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
        "--raw",
        action="store_true",
        help="Print raw JSON only.",
    )

    subparsers = parser.add_subparsers(dest="command", required=True)

    subparsers.add_parser("health", help="Call GET /health")
    subparsers.add_parser("list-intents", help="Call GET /v1/intents")

    create = subparsers.add_parser("create-intent", help="Create a new intent")
    create.add_argument("--user-address", required=True)
    create.add_argument("--source-chain-id", type=int, required=True)
    create.add_argument("--source-token-address", required=True)
    create.add_argument("--from-amount", required=True)
    create.add_argument("--sub-account-id", default="0")
    create.add_argument("--sub-account-name")
    create.add_argument("--create-account", choices=["true", "false"], default="true")
    create.add_argument("--target-account-address")
    create.add_argument("--from-amount-for-gas")
    create.add_argument("--slippage", type=float)

    create_direct = subparsers.add_parser("create-direct-intent", help="Create a direct HyperEVM funding intent")
    create_direct.add_argument("--user-address", required=True)
    create_direct.add_argument("--amount", required=True)
    create_direct.add_argument("--sub-account-id", default="0")
    create_direct.add_argument("--sub-account-name")
    create_direct.add_argument("--create-account", choices=["true", "false"], default="true")
    create_direct.add_argument("--target-account-address")

    get_intent = subparsers.add_parser("get-intent", help="Fetch one intent")
    get_intent.add_argument("--intent-id")

    submit = subparsers.add_parser(
        "submit-source-tx",
        help="Attach the source-chain tx hash to an intent",
    )
    submit.add_argument("--intent-id")
    submit.add_argument("--tx-hash", required=True)

    submit_direct = subparsers.add_parser(
        "submit-direct-tx",
        help="Attach the direct HyperEVM funding tx hash to a direct intent",
    )
    submit_direct.add_argument("--intent-id")
    submit_direct.add_argument("--tx-hash", required=True)

    process = subparsers.add_parser("process", help="Trigger immediate processing")
    process.add_argument("--intent-id")

    poll = subparsers.add_parser("poll", help="Poll an intent until it finishes or times out")
    poll.add_argument("--intent-id")
    poll.add_argument("--interval", type=float, default=5.0, help="Poll interval in seconds. Default: 5")
    poll.add_argument("--timeout", type=float, default=300.0, help="Timeout in seconds. Default: 300")

    subparsers.add_parser(
        "show-last-intent",
        help="Print the locally remembered intent ID",
    )

    return parser.parse_args()


def load_state(path: Path) -> Dict[str, Any]:
    if not path.exists():
        return {}
    return json.loads(path.read_text())


def save_state(path: Path, state: Dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(state, indent=2) + "\n")


def resolve_intent_id(args: argparse.Namespace, state: Dict[str, Any]) -> str:
    intent_id = getattr(args, "intent_id", None) or state.get("last_intent_id")
    if not intent_id:
        raise RuntimeError("No intent ID was provided and no previous intent ID was stored locally")
    return intent_id


def print_json(data: Any) -> None:
    print(json.dumps(data, indent=2))


def print_intent_summary(intent: Dict[str, Any]) -> None:
    print(f"intent_id: {intent['id']}")
    print(f"status: {intent['status']}")
    print(f"user_address: {intent['userAddress']}")
    print(f"escrow_address: {intent['escrowAddress']}")
    print(f"escrow_strategy: {intent['escrowStrategy']}")
    print(f"source_chain_id: {intent['sourceChainId']}")
    print(f"source_token: {intent['sourceTokenAddress']}")
    print(f"destination_token: {intent['destinationTokenAddress']}")
    print(f"from_amount: {intent['fromAmount']}")
    print(f"quoted_destination_amount: {intent['quotedDestinationAmount']}")
    print(f"minimum_amount: {intent['minimumAmount']}")
    if intent.get("subAccountName"):
        print(f"sub_account_name: {intent['subAccountName']}")
    print(f"create_account: {intent.get('createAccount')}")
    if intent.get("targetAccountAddress"):
        print(f"target_account_address: {intent['targetAccountAddress']}")

    optional_fields = [
        "sourceTxHash",
        "bridgeStatus",
        "receivedAmount",
        "createdAccountAddress",
        "destinationTxHash",
        "approvalTxHash",
        "createAccountTxHash",
        "depositTxHash",
        "failureReason",
    ]
    for field in optional_fields:
        value = intent.get(field)
        if value:
            print(f"{field}: {value}")

    history = intent.get("history") or []
    if history:
        print("history:")
        for item in history:
            print(f"  - {item['at']} | {item['status']} | {item['message']}")


def print_create_intent_help(intent: Dict[str, Any]) -> None:
    quote = intent.get("quote") or {}
    print()
    print("next steps:")
    print(f"1. Send the LI.FI bridge transaction from the user wallet to escrow {intent['escrowAddress']}.")
    if intent.get("createAccount") is False:
        print(f"   The backend will skip account creation and deposit into existing subaccount {intent['targetAccountAddress']}.")
    print("2. After the wallet transaction is mined, submit the source tx hash:")
    print(f"   python3 scripts/frontend_cli.py submit-source-tx --intent-id {intent['id']} --tx-hash 0x...")
    print("3. Poll the intent until it reaches COMPLETED or FAILED:")
    print(f"   python3 scripts/frontend_cli.py poll --intent-id {intent['id']}")
    if quote:
        print()
        print("quote snippet:")
        snippet = {
            "tool": quote.get("tool"),
            "action": quote.get("action"),
            "estimate": quote.get("estimate"),
            "transactionRequest": quote.get("transactionRequest"),
        }
        print_json(snippet)


def print_create_direct_intent_help(intent: Dict[str, Any]) -> None:
    print()
    print("next steps:")
    print(f"1. Send the configured HyperEVM collateral token to escrow {intent['escrowAddress']}.")
    print(f"   Required minimum amount for this intent: {intent['fromAmount']}")
    if intent.get("createAccount") is False:
        print(f"   The backend will skip account creation and deposit into existing subaccount {intent['targetAccountAddress']}.")
    print("2. After the HyperEVM transfer is mined, submit the tx hash:")
    print(f"   python3 scripts/frontend_cli.py submit-direct-tx --intent-id {intent['id']} --tx-hash 0x...")
    print("3. Poll the intent until it reaches COMPLETED or FAILED:")
    print(f"   python3 scripts/frontend_cli.py poll --intent-id {intent['id']}")


def main() -> int:
    args = parse_args()
    state = load_state(args.state_file)
    client = ApiClient(args.base_url, user_agent=args.user_agent)

    try:
        if args.command == "health":
            data = client.get("/health")
            print_json(data)
            return 0

        if args.command == "list-intents":
            data = client.get("/v1/intents")
            print_json(data)
            return 0

        if args.command == "show-last-intent":
            intent_id = state.get("last_intent_id")
            if not intent_id:
                raise RuntimeError(f"No intent ID found in {args.state_file}")
            print(intent_id)
            return 0

        if args.command == "create-intent":
            payload = {
                "userAddress": args.user_address,
                "sourceChainId": args.source_chain_id,
                "sourceTokenAddress": args.source_token_address,
                "fromAmount": args.from_amount,
                "subAccountId": args.sub_account_id,
                "subAccountName": args.sub_account_name or default_sub_account_name(args.sub_account_id),
                "createAccount": args.create_account == "true",
            }
            if args.target_account_address is not None:
                payload["targetAccountAddress"] = args.target_account_address
            if args.from_amount_for_gas is not None:
                payload["fromAmountForGas"] = args.from_amount_for_gas
            if args.slippage is not None:
                payload["slippage"] = args.slippage

            intent = client.post("/v1/intents", payload)
            state["last_intent_id"] = intent["id"]
            state["last_base_url"] = args.base_url
            save_state(args.state_file, state)

            if args.raw:
                print_json(intent)
            else:
                print_intent_summary(intent)
                print_create_intent_help(intent)
            return 0

        if args.command == "create-direct-intent":
            payload = {
                "userAddress": args.user_address,
                "amount": args.amount,
                "subAccountId": args.sub_account_id,
                "subAccountName": args.sub_account_name or default_sub_account_name(args.sub_account_id),
                "createAccount": args.create_account == "true",
            }
            if args.target_account_address is not None:
                payload["targetAccountAddress"] = args.target_account_address
            intent = client.post("/v1/direct-intents", payload)
            state["last_intent_id"] = intent["id"]
            state["last_base_url"] = args.base_url
            save_state(args.state_file, state)

            if args.raw:
                print_json(intent)
            else:
                print_intent_summary(intent)
                print_create_direct_intent_help(intent)
            return 0

        intent_id = resolve_intent_id(args, state)

        if args.command == "get-intent":
            intent = client.get(f"/v1/intents/{intent_id}")
            if args.raw:
                print_json(intent)
            else:
                print_intent_summary(intent)
            return 0

        if args.command == "submit-source-tx":
            intent = client.post(
                f"/v1/intents/{intent_id}/source-tx",
                {"txHash": args.tx_hash},
            )
            if args.raw:
                print_json(intent)
            else:
                print_intent_summary(intent)
            return 0

        if args.command == "submit-direct-tx":
            intent = client.post(
                f"/v1/direct-intents/{intent_id}/tx",
                {"txHash": args.tx_hash},
            )
            if args.raw:
                print_json(intent)
            else:
                print_intent_summary(intent)
            return 0

        if args.command == "process":
            intent = client.post(f"/v1/intents/{intent_id}/process")
            if args.raw:
                print_json(intent)
            else:
                print_intent_summary(intent)
            return 0

        if args.command == "poll":
            deadline = time.monotonic() + args.timeout
            while True:
                intent = client.get(f"/v1/intents/{intent_id}")
                if args.raw:
                    print_json(intent)
                else:
                    print_intent_summary(intent)
                    print()

                if intent["status"] in FINAL_STATUSES:
                    return 0 if intent["status"] == "COMPLETED" else 1

                if time.monotonic() >= deadline:
                    raise RuntimeError(
                        f"Polling timed out after {args.timeout} seconds while waiting for {intent_id}"
                    )

                time.sleep(args.interval)

        raise RuntimeError(f"Unsupported command: {args.command}")
    except KeyboardInterrupt:
        print("Interrupted", file=sys.stderr)
        return 130
    except Exception as exc:
        print(f"Error: {exc}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
