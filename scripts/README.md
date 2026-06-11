# Frontend Test Client

Use `frontend_cli.py` as a minimal frontend for the backend API.

For an interactive test flow that asks which chain you are using and then walks you through the correct backend path, use `test_flow_wizard.py`.

With `uv`:

```bash
uv run --with-requirements requirements.txt scripts/test_flow_wizard.py
```

If the backend is behind Cloudflare or another edge proxy that blocks Python's default `urllib` user agent, pass a browser-like header:

```bash
uv run --with-requirements requirements.txt scripts/test_flow_wizard.py \
  --base-url https://gasless.enigma.bz \
  --user-agent "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/137.0.0.0 Safari/537.36"
```

For the deployed host, use `https://gasless.enigma.bz`. Plain `http://gasless.enigma.bz` sits behind Cloudflare and returns `502`.

If the source wallet is EVM and you want to execute a returned LI.FI quote from Rabby, use `rabby_lifi_sender.html`:

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

If the source wallet is Solana, use Phantom for the source-side bridge transaction. This repo now includes an experimental Phantom helper, but you may still need your real Solana frontend or wallet if the LI.FI response shape does not include a serialized Solana transaction payload that the helper can execute.

There is now a separate experimental helper for Phantom:

1. Serve the `scripts/` directory locally:

```bash
python3 -m http.server 8000 --directory scripts
```

2. Open `http://127.0.0.1:8000/phantom_lifi_sender.html` in the browser where Phantom is installed.
3. Paste either the whole backend intent JSON or the nested LI.FI quote JSON.
4. Click `Parse`, then `Connect Phantom`.
5. Click `Send Transaction`.
6. Copy the returned Solana signature back into the terminal wizard as `sourceTxId`.

Important limitation:

- `phantom_lifi_sender.html` only works if the pasted LI.FI response already contains a serialized Solana transaction payload in a format the helper understands.
- If your LI.FI response does not contain that payload, use your real frontend or wallet instead and only use the CLI or wizard for backend submission and polling.

Local backend:

```bash
python3 scripts/frontend_cli.py health
python3 scripts/frontend_cli.py create-intent \
  --source-wallet-type solana \
  --source-wallet-address YourPhantomWallet \
  --owner-account-address 0xYourHyperOwner \
  --source-chain-id 1151111081099710 \
  --source-token-address EPjFWdd5AufqSSqeM2qN1xzybapC8G4wEGGkZwyTDt1v \
  --from-amount 25000000 \
  --from-amount-for-gas 1000000 \
  --sub-account-id 0 \
  --sub-account-name gasless-0 \
  --slippage 0.005
python3 scripts/frontend_cli.py submit-source-tx --source-tx-id YourSolanaSignature
python3 scripts/frontend_cli.py poll
```

Remote backend:

```bash
python3 scripts/frontend_cli.py --base-url https://your-server.example health
python3 scripts/frontend_cli.py --base-url https://your-server.example create-intent ...
```

For proxies that block Python's default `urllib` user agent, add `--user-agent "Mozilla/5.0 ..."` or set `FRONTEND_CLI_USER_AGENT`.

Notes:

- The client stores the last `intent_id` in `storage/frontend-client-state.json`.
- `poll` and `get-intent` use that stored ID if `--intent-id` is omitted.
- A fully successful end-to-end test still needs a real bridge tx and real HyperEVM execution unless the backend is mocked.

Direct HyperEVM frontend flow through the API client:

```bash
python3 scripts/frontend_cli.py create-direct-intent \
  --owner-account-address 0xYourWallet \
  --amount 20000000000000000000 \
  --sub-account-id 0 \
  --sub-account-name gasless-0
python3 scripts/frontend_cli.py submit-direct-tx --tx-hash 0xYourHyperEvmTransferTx
python3 scripts/frontend_cli.py poll
```

Deposit-only mode with an existing subaccount:

```bash
python3 scripts/frontend_cli.py create-direct-intent \
  --owner-account-address 0xYourWallet \
  --amount 20000000000000000000 \
  --sub-account-name gasless-0 \
  --create-account false \
  --target-account-address 0xExistingSubaccount
```

The CLI and wizard default `subAccountName` to `gasless-<sub-account-id>` for testing. Real frontend integrations should send the user-provided name explicitly.

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
