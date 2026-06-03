# Symmio Gasless Bootstrap PoC

This service implements the MVP from `SOL-396` as a Python backend on top of FastAPI and `web3.py`.

## What it does

1. Creates an intent for a user wallet.
2. Returns a LI.FI quote that bridges funds to a backend-controlled HyperEVM escrow address.
3. Waits for the frontend to submit the source-chain tx hash.
4. Polls LI.FI status until the bridge is completed.
5. Verifies that the minimum collateral arrived.
6. Sends `createAccountFor`.
7. Approves and deposits the collateral for the user.

It also supports a direct HyperEVM funding flow:

1. Creates a direct funding intent for a user wallet.
2. Returns the backend-controlled HyperEVM escrow address and collateral token.
3. Waits for the frontend to submit the HyperEVM ERC20 transfer tx hash.
4. Verifies on-chain that the tx transferred the configured collateral token from the user wallet to the escrow address with at least the required amount.
5. Creates the account and deposits the received collateral.

## Shared wallet vs per-user wallet

LI.FI documents destination address routing and transfer status polling, but this PoC does not depend on any arbitrary per-transfer metadata or memo field.

- `ESCROW_STRATEGY=shared` is the default. This works if the frontend creates an intent first and then submits the source tx hash for that intent.
- `ESCROW_STRATEGY=perUser` is also supported by deriving deterministic escrow wallets from a mnemonic.

For the MVP, the shared-wallet strategy is enough unless you explicitly want one destination address per user.

## Setup

1. Copy `.env.example` to `.env`.
2. Fill in the escrow credentials and HyperEVM RPC values.
3. Replace `contracts.example.json` with your real contract addresses and ABI fragments.
   For Hyperliquid production, you can use `contracts.prod.hyperliquid.json`.
4. Install dependencies:

```bash
python3 -m venv .venv
source .venv/bin/activate
pip install -r requirements.txt
```

5. Run the service:

```bash
uvicorn app.main:app --reload --host 0.0.0.0 --port 3000
```

## Persistence

Intent persistence now uses SQLite instead of a JSON file.

- Default database path: `./storage/intents.db`
- Configure it with `DATABASE_PATH`
- The Docker volume in `compose.yaml` already persists the `storage/` directory, so the SQLite database file will survive restarts

Legacy upgrade behavior:

- If `PERSISTENCE_FILE` points to an existing JSON file and the SQLite database is empty, the service imports those intents once during startup
- If `PERSISTENCE_FILE` already points to a `.db` file, it is treated as the database path instead of a legacy JSON source

This change removes the previous read/write race on the JSON persistence file and gives the service transactional storage.

## Contract config

The exact Symmio ABI is not present in this repo, so the service is config-driven.

`contracts.example.json` contains:

- `collateralToken`: the HyperEVM token expected in escrow and used for deposit.
- `createAccount`: contract call template for your `createAccountFor` flow.
- `approval`: optional ERC20 approval config before deposit.
- `deposit`: contract call template for your deposit flow.

Included ready-to-use configs:

- `contracts.stage.hyperliquid.json`
- `contracts.prod.hyperliquid.json`

Template variables available in `args` and `value`:

- `{{userAddress}}`
- `{{escrowAddress}}`
- `{{collateralToken}}`
- `{{receivedAmount}}`
- `{{minimumAmount}}`
- `{{subAccountId}}`
- `{{subAccountName}}`
- `{{sourceChainId}}`
- `{{sourceTxHash}}`

Amount units:

- `fromAmount` and `fromAmountForGas` are in the source token's smallest units.
- `MIN_COLLATERAL_AMOUNT` and `receivedAmount` are in the destination collateral token's smallest units.
- In the current stage config, the HyperEVM collateral token at `0x6aA554A167864027A02051D3F5C553244439B7Fd` has `18` decimals, so a `$20` minimum is `20000000000000000000`.

## API

### `POST /v1/intents`

Creates an intent and returns the LI.FI quote plus the HyperEVM escrow address.

```json
{
  "userAddress": "0x...",
  "sourceChainId": 42161,
  "sourceTokenAddress": "0xaf88d065e77c8cC2239327C5EDb3A432268e5831",
  "fromAmount": "25000000",
  "subAccountId": "0",
  "subAccountName": "alice-main",
  "createAccount": true,
  "targetAccountAddress": null,
  "fromAmountForGas": "1000000",
  "slippage": 0.005
}
```

`subAccountName` is optional. If omitted, the backend falls back to `gasless-<subAccountId>` for backward compatibility and test tooling.

### `POST /v1/intents/{intent_id}/source-tx`

Registers the user’s source-chain bridge transaction.

```json
{
  "txHash": "0x..."
}
```

### `POST /v1/direct-intents`

Creates a direct HyperEVM funding intent. The backend returns the escrow address and expects the user to transfer the configured collateral token on HyperEVM to that address.

```json
{
  "userAddress": "0x...",
  "amount": "20000000000000000000",
  "subAccountId": "0",
  "subAccountName": "alice-main",
  "createAccount": true,
  "targetAccountAddress": null
}
```

### `POST /v1/direct-intents/{intent_id}/tx`

Registers the user’s direct HyperEVM ERC20 transfer transaction.

```json
{
  "txHash": "0x..."
}
```

### `POST /v1/intents/{intent_id}/process`

Forces an immediate processing attempt.

### `GET /v1/intents/{intent_id}`

Returns the latest intent state, tx hashes, and failure details.

### `GET /v1/intents`

Returns all intents stored by the MVP.

### `GET /health`

Basic health endpoint.

## Frontend Test Client

There is a small CLI client in `scripts/frontend_cli.py` that acts like a minimal frontend against either your local backend or a deployed server.

Basic local flow:

```bash
python3 scripts/frontend_cli.py health
python3 scripts/frontend_cli.py create-intent \
  --user-address 0xYourWallet \
  --source-chain-id 42161 \
  --source-token-address 0xaf88d065e77c8cC2239327C5EDb3A432268e5831 \
  --from-amount 25000000 \
  --from-amount-for-gas 1000000 \
  --sub-account-id 0 \
  --sub-account-name gasless-0 \
  --slippage 0.005
python3 scripts/frontend_cli.py submit-source-tx --tx-hash 0xYourBridgeTxHash
python3 scripts/frontend_cli.py poll
```

For a remote backend, add `--base-url https://your-server.example`. Use `https://gasless.enigma.bz` for the deployed host; plain `http://gasless.enigma.bz` returns a proxy `502`.

The CLI defaults `subAccountName` to `gasless-<sub-account-id>` for testing. Real frontend integrations should send the user-provided name explicitly.

The client stores the last `intent_id` in `storage/frontend-client-state.json`, so `poll`, `process`, and `get-intent` can be run without passing `--intent-id` each time.

## Expected frontend flow

1. Call `POST /v1/intents`.
2. Execute the returned LI.FI transaction from the user wallet.
3. Call `POST /v1/intents/{intent_id}/source-tx` with the source tx hash.
4. Poll `GET /v1/intents/{intent_id}` until `status` becomes `COMPLETED` or `FAILED`.

## Expected direct HyperEVM flow

1. Call `POST /v1/direct-intents`.
2. Transfer the configured collateral token on HyperEVM from the user wallet to the returned `escrowAddress`.
3. Call `POST /v1/direct-intents/{intent_id}/tx` with that HyperEVM tx hash.
4. Poll `GET /v1/intents/{intent_id}` until `status` becomes `COMPLETED` or `FAILED`.

Direct-flow verification rules:

- The tx sender must match `userAddress`.
- The receipt must contain a `Transfer` event for the configured `collateralToken`.
- The transfer must go from `userAddress` to `escrowAddress`.
- The transferred amount must be at least the larger of the configured minimum and the requested direct intent amount.

Deposit-only mode:

- Set `createAccount` to `false` and provide `targetAccountAddress`.
- In that mode, the backend skips `createSubAccountsFor(...)` and deposits directly into the existing subaccount address.

## Important MVP limits

- No webhook integration.
- No production reconciliation or retry queue.
- No fee collection.
- Shared-wallet accounting is intent-driven, not ledger-grade.
- The service assumes ERC20 collateral for the deposit step.
