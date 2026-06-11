# Solana Funding Flow Design

This document describes the recommended Solana-first flow for this PoC.

The goal is:

- let the user start from a Solana wallet
- keep the user gasless on HyperEVM
- make the HyperEVM owner an explicit user input
- let the backend escrow wallet finish account creation and deposit

## Final Solution

The recommended architecture is:

1. The user logs in with a Solana wallet through Privy.
2. Privy provisions an embedded EVM wallet for the same user.
3. The frontend collects:
   - `solanaAddress`: source wallet and user-facing identity
   - `embeddedEvmAddress`: an available EVM owner option
   - `ownerAccountAddress`: the Hyper account owner selected by the user
4. The backend creates an intent and returns an `escrowAddress`.
5. The frontend executes a LI.FI route from the Solana wallet to the backend `escrowAddress` on HyperEVM.
6. The backend verifies that the expected collateral token arrived in escrow with enough amount.
7. The backend escrow wallet calls:
   - `create account` for `ownerAccountAddress`
   - `approve`
   - `deposit`
8. The backend marks the intent as completed and returns the created account address and transaction hashes.

In this design:

- the Solana wallet is the funding source
- the Hyper account owner is an explicit input
- the Privy embedded EVM wallet is one possible owner choice
- the escrow wallet remains the execution wallet

## Why This Flow

This flow matches the product requirement better than the current EVM-only model.

### What the current repo assumes

Today the backend assumes that one EVM address is all of the following:

- the source-chain funding wallet
- the user identity
- the HyperEVM account owner

That assumption works for EVM-only flows, but it breaks for a Solana-first UX.

### What changes in the Solana flow

For Solana support, those roles must be split:

- `signer/source wallet`: Solana wallet
- `owner account`: the explicit Hyper account owner selected by the user
- `executor`: backend escrow wallet

This keeps HyperEVM ownership flexible across all chains while letting the user start from Solana.

## User Experience

The intended UX is:

1. User clicks `Continue with Phantom`.
2. Privy signs the user in with the Solana wallet.
3. Privy creates or loads the user's embedded EVM wallet.
4. User chooses the Hyper account owner address.
5. The owner can be:
   - an existing EVM address the user already controls
   - the Privy embedded EVM wallet
6. User enters the deposit amount in the app.
7. Frontend asks the backend to create a Solana bridge intent.
8. Backend returns:
   - intent id
   - escrow address
   - expected destination token
   - minimum amount
   - bridge quote or route metadata
9. User confirms the Solana-side funding transaction in Phantom.
10. Backend waits for LI.FI completion and for funds to arrive in escrow.
11. Backend creates the HyperEVM account and deposits the collateral for the selected owner account.
12. Frontend shows the created subaccount address and completion state.

Important UX properties:

- the user does not need HYPE
- the user does not need to manually transfer from the embedded EVM wallet to escrow
- the user only performs the source-chain funding action

## Token Model

For the first version, the cleanest token path is:

- source asset: `USDC` on Solana
- source gas token: `SOL`
- destination asset: configured HyperEVM collateral token

`SOL` should be treated only as the source-chain gas token unless the bridge route swaps `SOL` into the required HyperEVM collateral token.

The backend should continue to assume that the final deposited asset on HyperEVM is the configured collateral token from the contract config.

## Backend Responsibilities

The backend should own:

- intent creation
- escrow address assignment
- persistence
- status polling
- bridge completion verification
- escrow balance verification
- HyperEVM execution

The frontend should own:

- Privy authentication
- collecting the Solana address and available owner address choices
- collecting the user-selected `ownerAccountAddress`
- executing the Solana-side LI.FI route
- submitting the source transaction id or route reference back to the backend

## Required Backend Changes

This flow should not reuse the current model unchanged.

### 1. Split source identity from owner identity

The current bridge flow should stop treating `userAddress` as the source wallet.

Instead, the intent model should distinguish between:

- `sourceWalletType`: `solana | evm`
- `sourceWalletAddress`
- `ownerAccountAddress`
- `ownerAccountType`: `evm`
- `availableOwnerAccounts`: optional frontend-only helper data if needed

Recommended bridge intent fields:

```json
{
  "sourceWalletType": "solana",
  "sourceWalletAddress": "base58-solana-pubkey",
  "ownerAccountAddress": "0xSelectedHyperOwner",
  "sourceChainId": "<solana-chain-id>",
  "sourceTokenAddress": "solana-token-or-mint-id",
  "fromAmount": "25000000",
  "subAccountId": "0",
  "subAccountName": "alice-main",
  "createAccount": true,
  "targetAccountAddress": null,
  "slippage": 0.005
}
```

The exact Solana chain and token identifiers should follow the LI.FI route format used by the frontend.

### 2. Use the selected owner account as the HyperEVM owner

For account creation and deposit, the execution context should use:

- `ownerAccountAddress` as the owner passed into `createAccount`
- `createdAccountAddress` or `targetAccountAddress` for deposit

In other words, the current contract placeholder `{{userAddress}}` should effectively map to the selected Hyper account owner for all intent types.

For non-EVM funding origins, the Privy embedded EVM wallet is the default recommended owner choice, but it is not the only possible one.

### 3. Relax direct sender assumptions for bridge intents

The bridge flow should not require:

- the source sender to be an EVM address
- the source sender to equal the owner account address

For Solana-origin bridge intents, the backend should verify the destination side only:

- route completed successfully
- destination chain is HyperEVM
- destination address equals `escrowAddress`
- destination token equals configured collateral token
- received amount is at least the required minimum

### 4. Generalize source transaction identifiers

The current bridge submission endpoint expects an EVM-style `0x...` hash.

For Solana flows, the source transaction identifier may be a Solana signature or another route-specific identifier. The model should move from `txHash` to a more general field such as:

- `sourceTxId`

Validation should depend on the source wallet or source chain type.

### 5. Verify the Privy user mapping

The backend should not blindly trust that the submitted Solana wallet and embedded EVM wallet belong to the same user.

Recommended validation:

- frontend sends a Privy auth token
- backend verifies the token
- backend stores a stable Privy user id on the intent
- backend confirms that the Solana wallet is linked to that same Privy user
- if `ownerAccountAddress` is the Privy embedded wallet, backend confirms that owner wallet is linked to that same Privy user

If the user selects a non-Privy external owner address, that ownership proof should be handled separately.

### 6. Keep the existing direct HyperEVM flow separate

The current `/v1/direct-intents` flow should remain EVM-only.

The Solana path is a bridge flow, not a direct HyperEVM funding flow.

## Recommended API Shape

The current `POST /v1/intents` endpoint can either be extended or versioned.

One acceptable shape is:

```json
{
  "sourceWalletType": "solana",
  "sourceWalletAddress": "7abc...",
  "ownerAccountAddress": "0x1234...",
  "sourceChainId": "<solana-chain-id>",
  "sourceTokenAddress": "EPjFWdd5AufqSSqeM2qN1xzybapC8G4wEGGkZwyTDt1v",
  "fromAmount": "25000000",
  "subAccountId": "0",
  "subAccountName": "alice-main",
  "createAccount": true,
  "targetAccountAddress": null,
  "fromAmountForGas": null,
  "slippage": 0.005
}
```

And the source transaction submission endpoint should become:

```json
{
  "sourceTxId": "solana-signature-or-route-id"
}
```

## Execution Rules

Once the bridge is complete:

1. backend confirms the funds arrived at escrow
2. backend confirms the amount is sufficient
3. backend uses the escrow wallet as execution account
4. backend calls `create account` for `ownerAccountAddress` if needed
5. backend approves the collateral token if needed
6. backend deposits the collateral into the created or target account

The selected owner account does not need to hold HYPE in this design because the backend escrow wallet remains the executor.

## Why Not Deposit Into The Embedded Wallet First

That alternative is possible, but it is worse for the primary requirement.

If funds land in the embedded EVM wallet first, the system needs one more HyperEVM transfer from the embedded wallet to escrow or needs to move account creation and deposit into the user wallet flow.

That adds:

- another transaction
- more approval/signing surface
- optional need for gas sponsorship
- more complex frontend orchestration

The direct-to-escrow bridge flow is simpler if the top priority is a gasless user experience.

## Non-Goals

This document does not propose:

- replacing the HyperEVM owner with the Solana address
- removing the escrow wallet from the bridge flow
- making the current EVM direct flow Solana-aware

Those are separate design choices.

## Summary

The final recommended Solana flow for this repo is:

- Solana wallet through Privy for login and source funding
- explicit `ownerAccountAddress` input for the Hyper account owner
- Privy embedded EVM wallet as one recommended owner choice for non-EVM funding flows
- LI.FI route from Solana to the backend escrow wallet
- backend escrow wallet performs account creation and deposit for the selected owner account

This is the right fit when the product goal is:

- Solana-first onboarding
- gasless HyperEVM execution
- minimal user actions after funding
