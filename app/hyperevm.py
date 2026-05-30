from __future__ import annotations

from typing import Any, Dict, List, Optional

from eth_account.signers.local import LocalAccount
from web3 import Web3
from web3.exceptions import TransactionNotFound


ERC20_READ_ABI = [
    {
        "type": "function",
        "name": "balanceOf",
        "stateMutability": "view",
        "inputs": [{"name": "account", "type": "address"}],
        "outputs": [{"name": "balance", "type": "uint256"}],
    },
    {
        "type": "function",
        "name": "allowance",
        "stateMutability": "view",
        "inputs": [
            {"name": "owner", "type": "address"},
            {"name": "spender", "type": "address"},
        ],
        "outputs": [{"name": "remaining", "type": "uint256"}],
    },
    {
        "type": "function",
        "name": "approve",
        "stateMutability": "nonpayable",
        "inputs": [
            {"name": "spender", "type": "address"},
            {"name": "amount", "type": "uint256"},
        ],
        "outputs": [{"name": "", "type": "bool"}],
    },
]
ERC20_TRANSFER_TOPIC = Web3.keccak(text="Transfer(address,address,uint256)").hex()


def _render_template(value: Any, context: Dict[str, Any]) -> Any:
    if isinstance(value, str):
        if value.startswith("{{") and value.endswith("}}"):
            return context[value[2:-2]]
        return value
    if isinstance(value, list):
        return [_render_template(item, context) for item in value]
    if isinstance(value, dict):
        return {key: _render_template(item, context) for key, item in value.items()}
    return value


def _coerce_abi_value(input_abi: Dict[str, Any], value: Any) -> Any:
    arg_type = input_abi["type"]
    if arg_type.endswith("[]"):
        inner_abi = dict(input_abi)
        inner_abi["type"] = arg_type[:-2]
        return [_coerce_abi_value(inner_abi, item) for item in value]
    if arg_type == "tuple":
        components = input_abi.get("components", [])
        if isinstance(value, dict):
            return tuple(_coerce_abi_value(component, value[component["name"]]) for component in components)
        return tuple(_coerce_abi_value(component, item) for component, item in zip(components, value))
    if arg_type.startswith("uint") or arg_type.startswith("int"):
        return int(value)
    if arg_type == "address":
        return Web3.to_checksum_address(value)
    if arg_type == "bool":
        return bool(value)
    return value


class HyperEvmService:
    def __init__(self, *, rpc_url: str, chain_id: int, native_symbol: str, min_gas_balance_wei: int) -> None:
        self.w3 = Web3(Web3.HTTPProvider(rpc_url))
        self.chain_id = chain_id
        self.native_symbol = native_symbol
        self.min_gas_balance_wei = min_gas_balance_wei

    def ensure_gas_balance(self, address: str) -> None:
        balance = self.w3.eth.get_balance(Web3.to_checksum_address(address))
        if balance < self.min_gas_balance_wei:
            raise RuntimeError(f"Escrow wallet {address} does not have enough {self.native_symbol} for execution")

    def get_transaction_receipt(self, tx_hash: str) -> Optional[Dict[str, Any]]:
        try:
            receipt = self.w3.eth.get_transaction_receipt(tx_hash)
        except TransactionNotFound:
            return None
        return dict(receipt)

    def get_transaction(self, tx_hash: str) -> Optional[Dict[str, Any]]:
        try:
            tx = self.w3.eth.get_transaction(tx_hash)
        except TransactionNotFound:
            return None
        return dict(tx)

    def get_token_balance(self, token: str, account: str) -> int:
        contract = self.w3.eth.contract(address=Web3.to_checksum_address(token), abi=ERC20_READ_ABI)
        return int(contract.functions.balanceOf(Web3.to_checksum_address(account)).call())

    def get_matching_transfer_amount(self, *, receipt: Dict[str, Any], token: str, from_address: str, to_address: str) -> int:
        token_address = Web3.to_checksum_address(token)
        expected_from = Web3.to_checksum_address(from_address)
        expected_to = Web3.to_checksum_address(to_address)
        matched_amount = 0

        for log in receipt.get("logs", []):
            if Web3.to_checksum_address(log["address"]) != token_address:
                continue

            topics = [topic.hex() if hasattr(topic, "hex") else str(topic) for topic in log.get("topics", [])]
            if len(topics) < 3 or topics[0].lower() != ERC20_TRANSFER_TOPIC.lower():
                continue

            log_from = Web3.to_checksum_address(f"0x{topics[1][-40:]}")
            log_to = Web3.to_checksum_address(f"0x{topics[2][-40:]}")
            if log_from != expected_from or log_to != expected_to:
                continue

            data = log.get("data", "0x0")
            data_hex = data.hex() if hasattr(data, "hex") else str(data)
            matched_amount += int(data_hex, 16)

        return matched_amount

    def ensure_approval(self, *, account: LocalAccount, token: str, amount: int, approval: Dict[str, Any]) -> Optional[str]:
        token_address = Web3.to_checksum_address(token)
        spender = Web3.to_checksum_address(approval["spender"])
        contract = self.w3.eth.contract(address=token_address, abi=ERC20_READ_ABI)
        allowance = int(contract.functions.allowance(account.address, spender).call())
        if allowance >= amount:
            return None

        approve_amount = (2**256 - 1) if approval.get("amountMode") == "max" else amount
        tx_hash = self._send_contract_function(
            account=account,
            contract=contract,
            function_name="approve",
            args=[spender, approve_amount],
            value=0,
        )
        return tx_hash

    def execute_template(self, *, account: LocalAccount, template: Dict[str, Any], context: Dict[str, Any]) -> Dict[str, Any]:
        contract = self.w3.eth.contract(
            address=Web3.to_checksum_address(template["address"]),
            abi=template["abi"],
        )
        rendered_args = _render_template(template["args"], context)
        function_abi = next(
            item
            for item in template["abi"]
            if item.get("type") == "function" and item.get("name") == template["functionName"]
        )
        coerced_args = [
            _coerce_abi_value(input_abi, arg)
            for input_abi, arg in zip(function_abi.get("inputs", []), rendered_args)
        ]
        rendered_value = _render_template(template.get("value", 0), context)
        tx_hash = self._send_contract_function(
            account=account,
            contract=contract,
            function_name=template["functionName"],
            args=coerced_args,
            value=int(rendered_value),
        )
        receipt = self._wait_for_receipt(tx_hash)
        captured = self._capture_from_receipt(contract, receipt, template.get("capture"))
        return {
            "tx_hash": tx_hash,
            "receipt": receipt,
            "captured": captured,
        }

    def _send_contract_function(
        self,
        *,
        account: LocalAccount,
        contract: Any,
        function_name: str,
        args: List[Any],
        value: int,
    ) -> str:
        function = getattr(contract.functions, function_name)(*args)
        nonce = self.w3.eth.get_transaction_count(account.address, "pending")
        gas_price = self.w3.eth.gas_price
        tx = function.build_transaction(
            {
                "from": account.address,
                "nonce": nonce,
                "value": value,
                "chainId": self.chain_id,
                "gasPrice": gas_price,
            }
        )

        if "gas" not in tx:
            tx["gas"] = int(function.estimate_gas({"from": account.address, "value": value}) * 1.2)

        signed = account.sign_transaction(tx)
        raw_transaction = getattr(signed, "raw_transaction", None) or getattr(signed, "rawTransaction")
        tx_hash = self.w3.eth.send_raw_transaction(raw_transaction)
        receipt = self._wait_for_receipt(tx_hash.hex())
        if receipt["status"] != 1:
            raise RuntimeError(f"HyperEVM transaction failed: {tx_hash.hex()}")
        return tx_hash.hex()

    def _wait_for_receipt(self, tx_hash: str) -> Dict[str, Any]:
        receipt = self.w3.eth.wait_for_transaction_receipt(tx_hash, timeout=180, poll_latency=2)
        return dict(receipt)

    def _capture_from_receipt(self, contract: Any, receipt: Dict[str, Any], capture: Optional[Dict[str, Any]]) -> Dict[str, Any]:
        if not capture:
            return {}

        event_name = capture["eventName"]
        field_name = capture["field"]
        store_as = capture["storeAs"]
        event = getattr(contract.events, event_name)()
        logs = event.process_receipt(receipt)
        if not logs:
            raise RuntimeError(f"Event {event_name} not found in receipt for capture")

        return {store_as: logs[0]["args"][field_name]}
