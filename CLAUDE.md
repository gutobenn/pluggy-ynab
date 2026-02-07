# pluggy-ynab

Syncs Brazilian bank transactions to YNAB using Pluggy as the data provider.

## How to run

```bash
python sync.py
```

Requires a `.env` file at the project root (see `.env.example`) and optionally `mappings.json` (see `mappings.example.json`).

## Project structure

- `sync.py` - Entry point. Loads env vars and mappings, initializes YNAB client, runs Pluggy importers, saves transactions.
- `ynab_importer.py` - Orchestrates importing: filters transactions by date, converts to YNAB format, batch saves.
- `importers/base.py` - Base class for Pluggy importers. Handles authentication, transaction fetching, and terminal output.
- `importers/credit_card.py` - Credit card transaction mapping (Apple subscriptions, iFood, Uber, PayPal, etc.).
- `importers/checking_account.py` - Checking account transaction mapping (transfers, debit purchases, document-based payee lookup).
- `importers/data_importer.py` - Abstract base class for importers (`DataImporter` with `get_data()` method).
- `importers/transaction.py` - `Transaction` TypedDict shared by all importers.
- `importers/util.py` - YNAB budget/account lookup helper.

## Config files

- `.env` - Secrets and env-specific settings (YNAB token, Pluggy credentials, account IDs). Gitignored.
- `mappings.json` - Personal transaction mappings (Apple subscriptions, iFood restaurants, document-based payees). Gitignored. Optional - the script works without it.

## Key concepts

- Transactions use millicents (amount * 1000) for precision
- Pluggy authentication and fetching are handled by the shared base class (`PluggyImporter`)
- The `Transaction` TypedDict is the shared format between importers and the YNAB exporter
- Env vars `CARD_ACCOUNT` and `CHECKING_ACCOUNT` are YNAB account names; `PLUGGY_CARD_ACCOUNT` and `PLUGGY_CHECKING_ACCOUNT` are Pluggy account IDs
- Mappings are loaded in sync.py and passed to importers via constructor
