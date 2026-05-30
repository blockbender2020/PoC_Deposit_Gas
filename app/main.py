from __future__ import annotations

import threading
from contextlib import asynccontextmanager
from typing import Optional

from fastapi import FastAPI, Request
from fastapi.responses import JSONResponse

from app.config import load_config
from app.errors import HttpError
from app.escrow import EscrowManager
from app.hyperevm import HyperEvmService
from app.lifi import LiFiClient
from app.models import (
    CreateDirectIntentRequest,
    CreateIntentRequest,
    SubmitDirectFundingTxRequest,
    SubmitSourceTxRequest,
)
from app.service import IntentService
from app.store import IntentStore


config = load_config()
store = IntentStore(config.persistence_file)
lifi_client = LiFiClient(
    base_url=config.lifi_base_url,
    api_key=config.lifi_api_key,
    integrator=config.lifi_integrator,
    to_chain_id=config.hyperevm_chain_id,
    to_token_address=config.contract_config["collateralToken"],
)
hyperevm_service = HyperEvmService(
    rpc_url=config.hyperevm_rpc_url,
    chain_id=config.hyperevm_chain_id,
    native_symbol=config.hyperevm_native_symbol,
    min_gas_balance_wei=config.min_gas_balance_wei,
)
escrow_manager = EscrowManager(
    strategy=config.escrow_strategy,
    shared_private_key=config.shared_escrow_private_key,
    mnemonic=config.escrow_mnemonic,
    hd_path_prefix=config.escrow_hd_path_prefix,
)
intent_service = IntentService(
    store=store,
    lifi_client=lifi_client,
    hyperevm_service=hyperevm_service,
    escrow_manager=escrow_manager,
    min_collateral_amount=config.min_collateral_amount,
    destination_token_address=config.contract_config["collateralToken"],
    destination_chain_id=config.hyperevm_chain_id,
    contract_config=config.contract_config,
)
stop_event = threading.Event()
worker_thread: Optional[threading.Thread] = None


def _worker_loop() -> None:
    while not stop_event.is_set():
        try:
            intent_service.process_pending()
        except Exception as exc:
            print(f"background processor failed: {exc}")
        stop_event.wait(config.poll_interval_ms / 1000)


@asynccontextmanager
async def lifespan(_: FastAPI):
    global worker_thread
    store.initialize()
    stop_event.clear()
    worker_thread = threading.Thread(target=_worker_loop, daemon=True)
    worker_thread.start()
    yield
    stop_event.set()
    if worker_thread:
        worker_thread.join(timeout=5)


app = FastAPI(title="Symmio Gasless Bootstrap PoC", lifespan=lifespan)


@app.exception_handler(HttpError)
async def http_error_handler(_: Request, exc: HttpError) -> JSONResponse:
    return JSONResponse(status_code=exc.status_code, content={"error": exc.message})


@app.exception_handler(Exception)
async def generic_error_handler(_: Request, exc: Exception) -> JSONResponse:
    return JSONResponse(status_code=500, content={"error": str(exc)})


@app.get("/health")
async def health() -> dict:
    return {
        "ok": True,
        "chainId": config.hyperevm_chain_id,
        "escrowStrategy": config.escrow_strategy,
        "minCollateralAmount": str(config.min_collateral_amount),
    }


@app.get("/v1/intents")
async def list_intents() -> list[dict]:
    return [item.model_dump() for item in intent_service.list_intents()]


@app.get("/v1/intents/{intent_id}")
async def get_intent(intent_id: str) -> dict:
    return intent_service.get_intent(intent_id).model_dump()


@app.post("/v1/intents", status_code=201)
async def create_intent(payload: CreateIntentRequest) -> dict:
    return intent_service.create_intent(payload).model_dump()


@app.post("/v1/direct-intents", status_code=201)
async def create_direct_intent(payload: CreateDirectIntentRequest) -> dict:
    return intent_service.create_direct_intent(payload).model_dump()


@app.post("/v1/intents/{intent_id}/source-tx")
async def submit_source_tx(intent_id: str, payload: SubmitSourceTxRequest) -> dict:
    return intent_service.submit_source_tx(intent_id, payload).model_dump()


@app.post("/v1/direct-intents/{intent_id}/tx")
async def submit_direct_funding_tx(intent_id: str, payload: SubmitDirectFundingTxRequest) -> dict:
    return intent_service.submit_direct_funding_tx(intent_id, payload).model_dump()


@app.post("/v1/intents/{intent_id}/process")
async def process_intent(intent_id: str) -> dict:
    intent_service.process_intent(intent_id)
    return intent_service.get_intent(intent_id).model_dump()
