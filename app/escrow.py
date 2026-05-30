from __future__ import annotations

from typing import List, Optional, Tuple

from eth_account import Account
from eth_account.signers.local import LocalAccount

from app.models import GaslessIntent


Account.enable_unaudited_hdwallet_features()


class EscrowManager:
    def __init__(
        self,
        *,
        strategy: str,
        shared_private_key: Optional[str],
        mnemonic: Optional[str],
        hd_path_prefix: str,
    ) -> None:
        self.strategy = strategy
        self.shared_account = Account.from_key(shared_private_key) if shared_private_key else None
        self.mnemonic = mnemonic
        self.hd_path_prefix = hd_path_prefix

    def assign_address(self, existing_intents: List[GaslessIntent]) -> Tuple[str, Optional[int]]:
        if self.strategy == "shared":
            if not self.shared_account:
                raise RuntimeError("Shared escrow account is not configured")
            return self.shared_account.address, None

        if not self.mnemonic:
            raise RuntimeError("Per-user escrow mnemonic is not configured")

        next_index = max((intent.escrowWalletIndex or -1 for intent in existing_intents), default=-1) + 1
        account = Account.from_mnemonic(self.mnemonic, account_path=f"{self.hd_path_prefix}{next_index}")
        return account.address, next_index

    def get_execution_account(self, intent: GaslessIntent) -> LocalAccount:
        if self.strategy == "shared":
            if not self.shared_account:
                raise RuntimeError("Shared escrow account is not configured")
            return self.shared_account

        if not self.mnemonic or intent.escrowWalletIndex is None:
            raise RuntimeError("Per-user escrow wallet cannot be derived")

        return Account.from_mnemonic(self.mnemonic, account_path=f"{self.hd_path_prefix}{intent.escrowWalletIndex}")
