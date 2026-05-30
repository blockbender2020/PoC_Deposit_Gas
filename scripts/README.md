# Frontend Test Client

Use `frontend_cli.py` as a minimal frontend for the backend API.

For an interactive test flow that asks which chain you are using and then walks you through the correct backend path, use `test_flow_wizard.py`.

With `uv`:

```bash
uv run --with-requirements requirements.txt scripts/test_flow_wizard.py
```

If you want to execute a returned LI.FI quote from Rabby, use `rabby_lifi_sender.html`:

1. Serve the `scripts/` directory locally:

```bash
python3 -m http.server 8000 --directory scripts
```

2. Open `http://127.0.0.1:8000/rabby_lifi_sender.html` in the browser where Rabby is installed.
3. Paste either the whole backend intent JSON or just the nested LI.FI quote JSON.
4. Click `Parse`, then `Connect Rabby`.
5. If the quote needs ERC20 approval, click `Approve Token` and wait for it to mine.
6. Click `Send Transaction`.
7. Copy the returned tx hash back into the terminal wizard.

Local backend:

```bash
python3 scripts/frontend_cli.py health
python3 scripts/frontend_cli.py create-intent \
  --user-address 0xYourWallet \
  --source-chain-id 42161 \
  --source-token-address 0xaf88d065e77c8cC2239327C5EDb3A432268e5831 \
  --from-amount 25000000 \
  --from-amount-for-gas 1000000 \
  --sub-account-id 0 \
  --slippage 0.005
python3 scripts/frontend_cli.py submit-source-tx --tx-hash 0xYourBridgeTxHash
python3 scripts/frontend_cli.py poll
```

Remote backend:

```bash
python3 scripts/frontend_cli.py --base-url https://your-server.example health
python3 scripts/frontend_cli.py --base-url https://your-server.example create-intent ...
```

Notes:

- The client stores the last `intent_id` in `storage/frontend-client-state.json`.
- `poll` and `get-intent` use that stored ID if `--intent-id` is omitted.
- A fully successful end-to-end test still needs a real bridge tx and real HyperEVM execution unless the backend is mocked.

Direct HyperEVM frontend flow through the API client:

```bash
python3 scripts/frontend_cli.py create-direct-intent \
  --user-address 0xYourWallet \
  --amount 20000000000000000000 \
  --sub-account-id 0
python3 scripts/frontend_cli.py submit-direct-tx --tx-hash 0xYourHyperEvmTransferTx
python3 scripts/frontend_cli.py poll
```

Deposit-only mode with an existing subaccount:

```bash
python3 scripts/frontend_cli.py create-direct-intent \
  --user-address 0xYourWallet \
  --amount 20000000000000000000 \
  --create-account false \
  --target-account-address 0xExistingSubaccount
```

## Manual HyperEVM Test

If you want to skip LI.FI and only test direct funding of the escrow wallet on HyperEVM, use `manual_hyperevm_test.py`.

Show the escrow address and collateral token:

```bash
python3 scripts/manual_hyperevm_test.py show-escrow
```

After you fund that escrow address with the configured collateral token on HyperEVM, run:

```bash
python3 scripts/manual_hyperevm_test.py run \
  --user-address 0xYourWallet \
  --received-amount 25000000000000000000
```

This creates a manual intent and directly executes account creation plus deposit without LI.FI status checks.

Note:

- `received-amount` here uses the destination collateral token's decimals, not the source-chain token decimals.
- In the current stage config, the collateral token has `18` decimals, so `$20` is `20000000000000000000`.
