from __future__ import annotations

import json
import os
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Dict, Optional

from dotenv import load_dotenv
from web3 import Web3


load_dotenv()


@dataclass(frozen=True)
class AppConfig:
    port: int
    host: str
    lifi_base_url: str
    lifi_api_key: Optional[str]
    lifi_integrator: str
    hyperevm_chain_id: int
    hyperevm_rpc_url: str
    hyperevm_native_symbol: str
    escrow_strategy: str
    shared_escrow_private_key: Optional[str]
    escrow_mnemonic: Optional[str]
    escrow_hd_path_prefix: str
    contract_config_path: Path
    min_collateral_amount: int
    min_gas_balance_wei: int
    persistence_file: Path
    poll_interval_ms: int
    contract_config: Dict[str, Any]


def _env(name: str, default: Optional[str] = None) -> str:
    value = os.getenv(name, default)
    if value is None:
        raise RuntimeError(f"Missing required environment variable: {name}")
    return value


def _checksum_address(value: str, field_name: str) -> str:
    if not Web3.is_address(value):
        raise RuntimeError(f"{field_name} must be a valid EVM address")
    return Web3.to_checksum_address(value)


def _load_contract_config(path: Path) -> Dict[str, Any]:
    raw = json.loads(path.read_text())
    raw["collateralToken"] = _checksum_address(raw["collateralToken"], "collateralToken")
    raw["createAccount"]["address"] = _checksum_address(raw["createAccount"]["address"], "createAccount.address")
    raw["deposit"]["address"] = _checksum_address(raw["deposit"]["address"], "deposit.address")

    approval = raw.get("approval")
    if approval:
        approval["spender"] = _checksum_address(approval["spender"], "approval.spender")

    return raw


def load_config() -> AppConfig:
    escrow_strategy = _env("ESCROW_STRATEGY", "shared")
    shared_escrow_private_key = os.getenv("SHARED_ESCROW_PRIVATE_KEY")
    escrow_mnemonic = os.getenv("ESCROW_MNEMONIC")

    if escrow_strategy == "shared" and not shared_escrow_private_key:
        raise RuntimeError("SHARED_ESCROW_PRIVATE_KEY is required when ESCROW_STRATEGY=shared")

    if escrow_strategy == "perUser" and not escrow_mnemonic:
        raise RuntimeError("ESCROW_MNEMONIC is required when ESCROW_STRATEGY=perUser")

    contract_config_path = Path(_env("CONTRACT_CONFIG_PATH", "./contracts.example.json")).resolve()

    return AppConfig(
        port=int(_env("PORT", "3000")),
        host=_env("HOST", "0.0.0.0"),
        lifi_base_url=_env("LI_FI_BASE_URL", "https://li.quest/v1"),
        lifi_api_key=os.getenv("LI_FI_API_KEY"),
        lifi_integrator=_env("LI_FI_INTEGRATOR", "symmio-gasless-poc"),
        hyperevm_chain_id=int(_env("HYPEREVM_CHAIN_ID", "999")),
        hyperevm_rpc_url=_env("HYPEREVM_RPC_URL", "https://rpc.hyperliquid.xyz/evm"),
        hyperevm_native_symbol=_env("HYPEREVM_NATIVE_SYMBOL", "HYPE"),
        escrow_strategy=escrow_strategy,
        shared_escrow_private_key=shared_escrow_private_key,
        escrow_mnemonic=escrow_mnemonic,
        escrow_hd_path_prefix=_env("ESCROW_HD_PATH_PREFIX", "m/44'/60'/0'/0/"),
        contract_config_path=contract_config_path,
        min_collateral_amount=int(_env("MIN_COLLATERAL_AMOUNT")),
        min_gas_balance_wei=int(_env("MIN_GAS_BALANCE_WEI", "1000000000000000")),
        persistence_file=Path(_env("PERSISTENCE_FILE", "./storage/intents.json")).resolve(),
        poll_interval_ms=int(_env("POLL_INTERVAL_MS", "15000")),
        contract_config=_load_contract_config(contract_config_path),
    )
