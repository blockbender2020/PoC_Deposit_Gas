from __future__ import annotations

from typing import Any, Dict, List, Literal, Optional

from pydantic import BaseModel, Field


IntentStatus = Literal[
    "AWAITING_BRIDGE",
    "BRIDGE_SUBMITTED",
    "BRIDGE_PENDING",
    "AWAITING_FUNDS",
    "FUNDING_SUBMITTED",
    "FUNDING_PENDING",
    "FUNDS_RECEIVED",
    "EXECUTING",
    "COMPLETED",
    "FAILED",
]

EscrowStrategy = Literal["shared", "perUser"]
IntentFlowType = Literal["bridge", "direct"]


class IntentEvent(BaseModel):
    at: str
    status: IntentStatus
    message: str


class GaslessIntent(BaseModel):
    id: str
    flowType: IntentFlowType = "bridge"
    userAddress: str
    createAccount: bool = True
    targetAccountAddress: Optional[str] = None
    sourceChainId: int
    sourceTokenAddress: str
    destinationTokenAddress: str
    fromAmount: str
    quotedDestinationAmount: str
    minimumAmount: str
    subAccountId: str
    subAccountName: Optional[str] = None
    escrowStrategy: EscrowStrategy
    escrowAddress: str
    escrowWalletIndex: Optional[int] = None
    status: IntentStatus
    quote: Dict[str, Any]
    quoteBridgeTool: Optional[str] = None
    createdAt: str
    updatedAt: str
    history: List[IntentEvent]
    sourceTxHash: Optional[str] = None
    bridgeStatus: Optional[str] = None
    receivedAmount: Optional[str] = None
    createdAccountAddress: Optional[str] = None
    destinationTxHash: Optional[str] = None
    approvalTxHash: Optional[str] = None
    createAccountTxHash: Optional[str] = None
    depositTxHash: Optional[str] = None
    failureReason: Optional[str] = None


class CreateIntentRequest(BaseModel):
    userAddress: str
    sourceChainId: int
    sourceTokenAddress: str
    fromAmount: str
    subAccountId: str = "0"
    subAccountName: Optional[str] = None
    createAccount: bool = True
    targetAccountAddress: Optional[str] = None
    fromAmountForGas: Optional[str] = None
    slippage: Optional[float] = Field(default=None, gt=0, lt=1)


class SubmitSourceTxRequest(BaseModel):
    txHash: str


class CreateDirectIntentRequest(BaseModel):
    userAddress: str
    amount: str
    subAccountId: str = "0"
    subAccountName: Optional[str] = None
    createAccount: bool = True
    targetAccountAddress: Optional[str] = None


class SubmitDirectFundingTxRequest(BaseModel):
    txHash: str
