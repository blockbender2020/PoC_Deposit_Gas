from __future__ import annotations

from typing import Any, Dict, Optional

import requests

from app.errors import HttpError


class LiFiClient:
    def __init__(
        self,
        *,
        base_url: str,
        integrator: str,
        to_chain_id: int,
        to_token_address: str,
        api_key: Optional[str] = None,
    ) -> None:
        self.base_url = base_url.rstrip("/")
        self.integrator = integrator
        self.to_chain_id = to_chain_id
        self.to_token_address = to_token_address
        self.session = requests.Session()
        if api_key:
            self.session.headers.update({"x-lifi-api-key": api_key})

    def get_quote(self, request: Dict[str, Any], to_address: str) -> Dict[str, Any]:
        params = {
            "fromChain": request["sourceChainId"],
            "toChain": self.to_chain_id,
            "fromToken": request["sourceTokenAddress"],
            "toToken": self.to_token_address,
            "fromAmount": request["fromAmount"],
            "fromAddress": request["sourceWalletAddress"],
            "toAddress": to_address,
            "integrator": self.integrator,
            "allowDestinationCall": "false",
        }

        if request.get("fromAmountForGas"):
            params["fromAmountForGas"] = request["fromAmountForGas"]

        if request.get("slippage") is not None:
            params["slippage"] = request["slippage"]

        return self._get("/quote", params)

    def get_status(
        self,
        *,
        tx_hash: str,
        from_chain_id: int,
        to_chain_id: int,
        bridge: Optional[str] = None,
    ) -> Dict[str, Any]:
        params = {
            "txHash": tx_hash,
            "fromChain": from_chain_id,
            "toChain": to_chain_id,
        }
        if bridge:
            params["bridge"] = bridge
        return self._get("/status", params)

    def _get(self, path: str, params: Dict[str, Any]) -> Dict[str, Any]:
        response = self.session.get(f"{self.base_url}{path}", params=params, timeout=30)
        if not response.ok:
            raise HttpError(response.status_code, f"LI.FI request failed: {response.text}")
        return response.json()
