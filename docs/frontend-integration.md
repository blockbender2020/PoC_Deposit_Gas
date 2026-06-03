# Frontend Integration Guide

This document explains how a web frontend should integrate with the gasless deposit backend in this repo.

The backend supports two user flows:

1. `bridge`: the user starts on another chain, receives a LI.FI quote, executes that transaction in their wallet, and the backend finishes the HyperEVM deposit after the bridge completes.
2. `direct`: the user is already on HyperEVM and transfers the configured collateral token directly to the backend escrow address, then the backend creates the account and deposits the funds.

## Base URL

Your frontend should talk to the backend root, for example:

- Local: `http://127.0.0.1:3000`
- Remote: `https://gasless.enigma.bz`

Use `https://` for the deployed host. `http://gasless.enigma.bz` returns a proxy `502`.

There is no HTML app at `/`. The API starts at:

- `GET /health`
- `POST /v1/intents`
- `POST /v1/direct-intents`

## Health Check

Use this on app startup to learn the target chain and configured minimum collateral:

`GET /health`

Example response:

```json
{
  "ok": true,
  "chainId": 999,
  "escrowStrategy": "shared",
  "minCollateralAmount": "10000"
}
```

Frontend use:

- Check the backend is reachable.
- Read `chainId` to confirm the HyperEVM destination chain.
- Read `minCollateralAmount` to validate user-entered amounts before submitting.

## Flow Selection

Choose the flow from the user’s current chain context:

- If the user is funding from another chain, use the `bridge` flow.
- If the user is already on HyperEVM with the configured collateral token, use the `direct` flow.

## Bridge Flow

### 1. Create an intent

`POST /v1/intents`

Request body:

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

Field notes:

- `userAddress`: the wallet that will sign and send the bridge transaction.
- `sourceChainId`: the chain the user is funding from.
- `sourceTokenAddress`: the token on the source chain.
- `fromAmount`: source token amount in smallest units.
- `subAccountId`: forwarded into the on-chain execution context.
- `subAccountName`: the frontend-provided subaccount name to use during account creation.
- `createAccount`: if `true`, the backend creates the target account before deposit.
- `targetAccountAddress`: required when `createAccount` is `false`.
- `fromAmountForGas`: optional LI.FI gas-on-destination parameter.
- `slippage`: optional decimal between `0` and `1`.

Validation rules enforced by the backend:

- `userAddress` must be a valid EVM address.
- `sourceTokenAddress` must be a valid EVM address.
- `fromAmount` and `fromAmountForGas` must be integer strings.
- `targetAccountAddress` is required when `createAccount` is `false`.
- The LI.FI quoted destination amount must be at least `minCollateralAmount`.

`subAccountName` is optional for backward compatibility, but frontend integrations should send it explicitly. If omitted, the backend falls back to `gasless-<subAccountId>`.

### 2. Use the returned quote

The response is a full intent object. The important fields for the frontend are:

- `id`
- `status`
- `escrowAddress`
- `destinationTokenAddress`
- `minimumAmount`
- `quotedDestinationAmount`
- `quote`
- `quoteBridgeTool`

The frontend should use `quote` to drive the wallet action. The backend requests the quote from LI.FI with:

- destination chain = configured HyperEVM chain
- destination token = configured collateral token
- destination address = the returned `escrowAddress`
- `allowDestinationCall = false`

That means the frontend should execute the quote transaction as a plain bridge/swap-to-escrow flow, not as an arbitrary destination contract call.

### 3. Wait for the wallet transaction hash

After the user signs and broadcasts the source-chain transaction, collect the tx hash from the wallet.

Then call:

`POST /v1/intents/{intent_id}/source-tx`

Request body:

```json
{
  "txHash": "0x..."
}
```

The backend immediately marks the intent as submitted and starts processing.

### 4. Poll for final state

Poll:

`GET /v1/intents/{intent_id}`

Stop polling when:

- `status === "COMPLETED"`
- `status === "FAILED"`

A 5 to 10 second polling interval is reasonable for a browser client.

## Direct HyperEVM Flow

### 1. Create a direct funding intent

`POST /v1/direct-intents`

Request body:

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

Field notes:

- `amount` is the HyperEVM collateral token amount in smallest units.
- `subAccountName` is the name that will be used if the backend creates a new subaccount.
- The backend rejects values below `minCollateralAmount`.
- If `createAccount` is `false`, `targetAccountAddress` is required.

### 2. Show funding instructions

Use the response to instruct the user to transfer:

- token: `destinationTokenAddress`
- amount: at least `fromAmount`
- recipient: `escrowAddress`
- chain: HyperEVM from `sourceChainId` or `/health.chainId`

### 3. Register the HyperEVM transfer

After the wallet transfer is mined or at least broadcast, collect the tx hash and call:

`POST /v1/direct-intents/{intent_id}/tx`

Request body:

```json
{
  "txHash": "0x..."
}
```

### 4. Poll for final state

Poll:

`GET /v1/intents/{intent_id}`

The same final states apply:

- `COMPLETED`
- `FAILED`

## Intent Object

Both flows return the same intent shape.

Important fields:

```ts
type IntentStatus =
  | "AWAITING_BRIDGE"
  | "BRIDGE_SUBMITTED"
  | "BRIDGE_PENDING"
  | "AWAITING_FUNDS"
  | "FUNDING_SUBMITTED"
  | "FUNDING_PENDING"
  | "FUNDS_RECEIVED"
  | "EXECUTING"
  | "COMPLETED"
  | "FAILED";

type IntentFlowType = "bridge" | "direct";

type IntentEvent = {
  at: string;
  status: IntentStatus;
  message: string;
};

type GaslessIntent = {
  id: string;
  flowType: IntentFlowType;
  userAddress: string;
  createAccount: boolean;
  targetAccountAddress: string | null;
  sourceChainId: number;
  sourceTokenAddress: string;
  destinationTokenAddress: string;
  fromAmount: string;
  quotedDestinationAmount: string;
  minimumAmount: string;
  subAccountId: string;
  subAccountName?: string | null;
  escrowStrategy: "shared" | "perUser";
  escrowAddress: string;
  escrowWalletIndex: number | null;
  status: IntentStatus;
  quote: Record<string, unknown>;
  quoteBridgeTool?: string | null;
  createdAt: string;
  updatedAt: string;
  history: IntentEvent[];
  sourceTxHash?: string | null;
  bridgeStatus?: string | null;
  receivedAmount?: string | null;
  createdAccountAddress?: string | null;
  destinationTxHash?: string | null;
  approvalTxHash?: string | null;
  createAccountTxHash?: string | null;
  depositTxHash?: string | null;
  failureReason?: string | null;
};
```

## Status Semantics

Recommended UI meanings:

- `AWAITING_BRIDGE`: bridge quote is ready; waiting for the user to send the source-chain transaction.
- `BRIDGE_SUBMITTED`: frontend submitted the bridge tx hash.
- `BRIDGE_PENDING`: LI.FI has not finished the bridge yet.
- `AWAITING_FUNDS`: direct funding intent created; waiting for the user to transfer tokens on HyperEVM.
- `FUNDING_SUBMITTED`: frontend submitted the HyperEVM transfer tx hash.
- `FUNDING_PENDING`: the direct funding tx is not mined yet.
- `FUNDS_RECEIVED`: escrow has received enough funds and backend execution is starting or about to start.
- `EXECUTING`: backend is sending HyperEVM transactions for account creation, approval, and deposit.
- `COMPLETED`: the flow finished successfully.
- `FAILED`: the flow stopped permanently; inspect `failureReason` and `history`.

`history` is append-only and is the best field to show detailed progress in the UI.

## What the Backend Verifies

Bridge flow:

- The submitted source tx hash is unique across intents.
- LI.FI status eventually reports a completed bridge.
- The received amount on HyperEVM is at least the configured minimum.
- The bridge completed to the expected escrow address on the expected destination chain.

Direct flow:

- The submitted tx hash is unique across intents.
- The HyperEVM tx exists and succeeds.
- The tx sender matches `userAddress`.
- The receipt contains an ERC20 `Transfer` for `destinationTokenAddress`.
- The transfer goes from `userAddress` to `escrowAddress`.
- The received amount is at least `max(minimumAmount, fromAmount)`.

## Error Handling

Expected backend error shapes:

```json
{
  "error": "message"
}
```

Typical HTTP statuses:

- `400`: invalid input, below-minimum amount, invalid address, invalid tx hash.
- `404`: unknown intent.
- `409`: duplicate transaction hash already attached to another intent.
- `500`: unexpected backend failure.
- `502`: upstream LI.FI problem or invalid LI.FI response.

Frontend guidance:

- Show the backend message directly for operator-facing tools.
- For end users, map technical messages to clearer UI copy but keep the raw message available in logs.
- On `FAILED`, render `failureReason` and the latest `history` entry.

## Browser Integration Notes

- A normal browser frontend should not hit the Cloudflare user-agent issue that affected the Python CLI, because browsers already send browser user agents.
- If your frontend is server-side and calls the API from a backend process, make sure your HTTP client is not blocked by any edge bot rules.
- The backend does not expose a webhook for completion. Polling is required.
- The backend does not provide token metadata or decimals. Your frontend should source token metadata separately.

## Minimal Browser Sequence

Bridge flow:

1. `GET /health`
2. User chooses source chain and token.
3. `POST /v1/intents`
4. Execute `intent.quote` in the wallet.
5. `POST /v1/intents/{id}/source-tx`
6. Poll `GET /v1/intents/{id}` until `COMPLETED` or `FAILED`

Direct flow:

1. `GET /health`
2. User switches to HyperEVM.
3. `POST /v1/direct-intents`
4. User transfers `destinationTokenAddress` to `escrowAddress`
5. `POST /v1/direct-intents/{id}/tx`
6. Poll `GET /v1/intents/{id}` until `COMPLETED` or `FAILED`

## Example Fetch Helpers

```ts
async function api<T>(baseUrl: string, path: string, init?: RequestInit): Promise<T> {
  const response = await fetch(`${baseUrl}${path}`, {
    ...init,
    headers: {
      "Content-Type": "application/json",
      ...(init?.headers ?? {}),
    },
  });

  const text = await response.text();
  const data = text ? JSON.parse(text) : {};

  if (!response.ok) {
    throw new Error(data.error ?? `HTTP ${response.status}`);
  }

  return data as T;
}

async function createBridgeIntent(baseUrl: string, payload: Record<string, unknown>) {
  return api<GaslessIntent>(baseUrl, "/v1/intents", {
    method: "POST",
    body: JSON.stringify(payload),
  });
}

async function submitSourceTx(baseUrl: string, intentId: string, txHash: string) {
  return api<GaslessIntent>(baseUrl, `/v1/intents/${intentId}/source-tx`, {
    method: "POST",
    body: JSON.stringify({ txHash }),
  });
}

async function getIntent(baseUrl: string, intentId: string) {
  return api<GaslessIntent>(baseUrl, `/v1/intents/${intentId}`);
}
```

## Integration Checklist

- Read `GET /health` on startup.
- Validate addresses client-side before submit.
- Convert token amounts to smallest units before calling the backend.
- Persist `intent.id` locally so the user can resume progress after refresh.
- Send `subAccountName` explicitly from the frontend instead of relying on the backend fallback.
- Persist the wallet tx hash until the backend confirms it.
- Poll until `COMPLETED` or `FAILED`.
- Show `history` as the source of truth for progress updates.
- Treat `quote` as opaque data from the backend and pass it to your wallet execution layer.
