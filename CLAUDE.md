# pluggy-ynab

Syncs Brazilian bank transactions to YNAB using Pluggy as the data provider.

## How to run

```bash
python sync.py                       # imports the last 14 days
python sync.py --from 2026-01-01
python sync.py --dry-run             # fetch + print everything, don't save to YNAB
python sync.py --dry-run --debug     # also print per-page fetch counts vs Pluggy's total
python sync.py --list-accounts <ITEM_ID>   # discover Pluggy account ids for accounts.json, then exit
python sync.py --update-investments  # post the YNAB↔Pluggy diff per investment account as a "Rendimento" txn
```

Requires a `.env` (secrets, see `.env.example`) and an `accounts.json` (list of accounts to sync, see `accounts.example.json`) at the project root. `mappings.json` is optional (see `mappings.example.json`).

Every run ends with a balance-reconciliation table (grouped by type: checking / credit cards / investments) comparing each account's current Pluggy balance to its YNAB balance (cleared + uncleared), flagging match/MISMATCH (credit cards sign-inverted). Transaction fetching paginates through all pages (Pluggy caps a page at 500). Investment accounts are **report-only by default** (Pluggy exposes only position balances, no transactions); with `--update-investments` the per-account difference is posted as a single "Rendimento" adjustment via `YNABTransactionImporter.add_adjustment` (`import_id` `REND-<date>-<acct8>` → idempotent per day).

## Project structure

- `sync.py` - Entry point. Loads `.env` + `accounts.json` + `mappings.json`, initializes the YNAB client, then instantiates an importer per account via the `IMPORTERS` type→class registry. Since the run is network-bound, accounts are **fetched from Pluggy concurrently** (`ThreadPoolExecutor`, up to 8 workers); a startup banner + "Connecting to YNAB…" line cover the otherwise-silent init, and a live `✓ [k/N] <account>` counter prints as each fetch completes (via `safe_print`) so the parallel wait isn't blank. Results are then processed sequentially on the main thread (in config order) — detailed per-account transaction dumps via `importer.print_transactions()`, then queueing — so the shared transaction list and YNAB writes stay race-free. Each account is wrapped in try/except (setup and fetch) so one failure doesn't abort the run. Flags: `--from`, `--dry-run`/`-n` (skip save), `--debug` (verbose), `--list-accounts <ITEM_ID>` (discovery helper, auths to Pluggy and lists accounts/investments, then exits), `--update-investments` (post the per-investment-account Pluggy↔YNAB diff as a "Rendimento" adjustment). Ends with `print_reconciliation()` (Pluggy vs YNAB balances, grouped by type).
- `ynab_importer.py` - Orchestrates importing: `add_transactions()` filters already-fetched transactions by date and queues them (kept separate from the network fetch so accounts can be fetched concurrently); `get_transactions_from()` is the fetch-then-queue convenience wrapper; converts to YNAB format and batch saves.
- `importers/base.py` - `PluggyImporter` (auth, amount→millicents, `get_data()` flow with abstract `_fetch_raw`/`_map_transaction` — runs in a worker thread so it does no console output beyond opt-in `--debug` lines; the caller prints the dump afterwards via `print_transactions()`, `_fetch_paginated` helper that loops all pages, `_fetch_balance` hook, `pluggy_balance` attr). Module-level `safe_print()` (guarded by `_PRINT_LOCK`) is used for any output emitted during the concurrent fetch (debug lines, the progress counter) so threads don't interleave. Also `AccountTransactionsImporter` (fetches BANK/CREDIT accounts via paginated `GET /transactions?accountId`, balance via `GET /accounts/{id}`).
- `importers/credit_card.py` - Credit card mapping. Nubank-specific cleanup (Apple, iFood, Uber, PayPal, etc.) is gated behind `bank == 'nubank'`; other banks use the raw description.
- `importers/checking_account.py` - Checking mapping. Cross-bank `document_payees` lookup (by payer/receiver CPF/CNPJ) runs for all banks; Nubank-specific text parsing is gated behind `bank == 'nubank'`.
- `importers/investment.py` - `PluggyInvestmentData`, **report-only**. `pluggy_source` is a Pluggy *item* id; `_fetch_raw` returns `[]` (no transactions to import), `_fetch_balance` lists `GET /investments?itemId`, keeps positions matching `investment_filter` (`_matches`: AND of optional `type`/`subtype`/`rate`, each value-or-list), and sums their `balance` into `pluggy_balance`. So investment accounts only feed the reconciliation table; the user fixes YNAB manually.
- `importers/data_importer.py` - Abstract base class for importers (`DataImporter` with `get_data()` method).
- `importers/transaction.py` - `Transaction` TypedDict shared by all importers.
- `importers/util.py` - YNAB budget/account lookup helper (`find_by_name`).

## Config files

- `.env` - Secrets only: `YNAB_TOKEN`, `YNAB_BUDGET`, `PLUGGY_CLIENT_ID`, `PLUGGY_CLIENT_SECRET`. Gitignored.
- `accounts.json` - List of accounts to sync. Each entry: `bank`, `type` (`checking`/`credit_card`/`investment`), `ynab_account` (YNAB account name), and `pluggy_account_id` (checking/credit_card) or `pluggy_item_id` (investment). Investment entries take an optional `investment_filter` (`type`/`subtype`/`rate`, value-or-list, AND-combined) selecting which of the item's positions count toward that account. Optional `enabled: false` to skip. Gitignored.
- `mappings.json` - Personal transaction mappings (Apple subscriptions, iFood restaurants, document-based payees). Gitignored. Optional - the script works without it.

## Key concepts

- Transactions use millicents (amount * 1000) for precision
- Pluggy auth uses one `PLUGGY_CLIENT_ID`/`SECRET` (one Pluggy application) for all banks/accounts; each bank is a connected *item* with its own accounts
- The `Transaction` TypedDict is the shared format between importers and the YNAB exporter
- A YNAB account is resolved by name (`ynab_account` in `accounts.json`); the importer writes to that account's id
- The `bank` field selects bank-specific parsing — only Nubank rules exist today; other banks fall back to generic behavior until per-bank rules are added
- Investments are NOT a Pluggy account type — they live under a separate `/investments` endpoint keyed by item id, and expose only a current balance per position (no transactions). They're report-only by default (summed/filtered, shown in the reconciliation table); `--update-investments` posts the diff vs YNAB as a single "Rendimento" transaction
- Mappings are loaded in sync.py and passed to importers via constructor
