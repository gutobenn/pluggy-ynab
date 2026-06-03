# pluggy-ynab

Syncs Brazilian bank transactions to YNAB using Pluggy as the data provider.

## How to run

```bash
python sync.py               # imports the last 30 days
python sync.py --from 2026-01-01
```

Requires a `.env` (secrets, see `.env.example`) and an `accounts.json` (list of accounts to sync, see `accounts.example.json`) at the project root. `mappings.json` is optional (see `mappings.example.json`).

## Project structure

- `sync.py` - Entry point. Loads `.env` + `accounts.json` + `mappings.json`, initializes the YNAB client, then loops over the configured accounts, instantiating an importer per account via the `IMPORTERS` type→class registry. Each account is wrapped in try/except so one failure doesn't abort the run.
- `ynab_importer.py` - Orchestrates importing: filters transactions by date, converts to YNAB format, batch saves.
- `importers/base.py` - `PluggyImporter` (auth, amount→millicents, terminal output, `get_data()` flow with abstract `_fetch_raw`/`_map_transaction`) and `AccountTransactionsImporter` (fetches BANK/CREDIT accounts via `GET /transactions?accountId`).
- `importers/credit_card.py` - Credit card mapping. Nubank-specific cleanup (Apple, iFood, Uber, PayPal, etc.) is gated behind `bank == 'nubank'`; other banks use the raw description.
- `importers/checking_account.py` - Checking mapping. Cross-bank `document_payees` lookup (by payer/receiver CPF/CNPJ) runs for all banks; Nubank-specific text parsing is gated behind `bank == 'nubank'`.
- `importers/investment.py` - `PluggyInvestmentData`. `pluggy_source` is a Pluggy *item* id; lists `GET /investments?itemId` and aggregates each investment's `GET /investments/{id}/transactions`. Sign by movement type (`OUTFLOW_TYPES = {SELL, TAX}`).
- `importers/data_importer.py` - Abstract base class for importers (`DataImporter` with `get_data()` method).
- `importers/transaction.py` - `Transaction` TypedDict shared by all importers.
- `importers/util.py` - YNAB budget/account lookup helper (`find_by_name`).

## Config files

- `.env` - Secrets only: `YNAB_TOKEN`, `YNAB_BUDGET`, `PLUGGY_CLIENT_ID`, `PLUGGY_CLIENT_SECRET`. Gitignored.
- `accounts.json` - List of accounts to sync. Each entry: `bank`, `type` (`checking`/`credit_card`/`investment`), `ynab_account` (YNAB account name), and `pluggy_account_id` (checking/credit_card) or `pluggy_item_id` (investment). Optional `enabled: false` to skip. Gitignored.
- `mappings.json` - Personal transaction mappings (Apple subscriptions, iFood restaurants, document-based payees). Gitignored. Optional - the script works without it.

## Key concepts

- Transactions use millicents (amount * 1000) for precision
- Pluggy auth uses one `PLUGGY_CLIENT_ID`/`SECRET` (one Pluggy application) for all banks/accounts; each bank is a connected *item* with its own accounts
- The `Transaction` TypedDict is the shared format between importers and the YNAB exporter
- A YNAB account is resolved by name (`ynab_account` in `accounts.json`); the importer writes to that account's id
- The `bank` field selects bank-specific parsing — only Nubank rules exist today; other banks fall back to generic behavior until per-bank rules are added
- Investments are NOT a Pluggy account type — they live under a separate `/investments` endpoint keyed by item id
- Mappings are loaded in sync.py and passed to importers via constructor
