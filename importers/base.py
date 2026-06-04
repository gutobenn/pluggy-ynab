import abc
from typing import List

import requests
from rich.markup import escape

from ui import console
from .data_importer import DataImporter
from .transaction import Transaction

PLUGGY_API = "https://api.pluggy.ai"
PAGE_SIZE = 500


class PluggyImporter(DataImporter):
    """Base class for any Pluggy-backed importer.

    Subclasses implement ``_fetch_raw`` (how to pull raw records from Pluggy)
    and ``_map_transaction`` (how to turn one raw record into a Transaction).
    They may override ``_fetch_balance`` to expose the account's current
    balance for reconciliation.
    """

    def __init__(self, *, name: str, bank: str, account_id: str,
                 client_id: str, client_secret: str, pluggy_source: str,
                 start_import_date: str, mappings: dict = None, debug: bool = False,
                 investment_filter: dict = None):
        self.name = name                       # YNAB account name, used as display label
        self.bank = bank                       # e.g. "nubank" — selects bank-specific parsing
        self.account_id = account_id           # YNAB account id (destination)
        self.pluggy_client_id = client_id
        self.pluggy_client_secret = client_secret
        self.pluggy_source = pluggy_source     # Pluggy account id (bank/credit) or item id (investment)
        self.start_import_date = start_import_date
        self.mappings = mappings or {}
        self.debug = debug                     # verbose per-page fetch diagnostics
        self.investment_filter = investment_filter or {}  # used by PluggyInvestmentData only
        self.pluggy_balance = None             # current balance reported by Pluggy (account currency)

    def get_data(self) -> List[Transaction]:
        # Runs in a worker thread (accounts are fetched in parallel), so it does
        # no console output of its own beyond opt-in --debug diagnostics. The
        # caller renders the per-account dump afterwards via ui.render_transactions().
        api_key = self._authenticate()
        raw_transactions = self._fetch_raw(api_key)
        self.pluggy_balance = self._fetch_balance(api_key)
        return [self._map_transaction(t) for t in raw_transactions]

    def get_balance(self):
        """Populate ``self.pluggy_balance`` using balance endpoints only — no
        transactions fetched. Mirrors get_data (runs in a worker thread) for the
        reconcile-only flow; returns the balance so the caller can display it."""
        api_key = self._authenticate()
        self.pluggy_balance = self._fetch_reconcile_balance(api_key)
        return self.pluggy_balance

    def diagnose(self) -> dict:
        """Authenticate and probe this account's connection for the doctor command.
        Mirrors get_data (runs in a worker thread) and never raises — failures are
        returned as ``{'ok': False, 'error': ...}``."""
        try:
            api_key = self._authenticate()
            info = self.check_connection(api_key)
            info.setdefault('ok', True)
            return info
        except Exception as e:
            return {'ok': False, 'error': str(e)}

    @abc.abstractmethod
    def _fetch_raw(self, api_key: str) -> list:
        raise NotImplementedError

    @abc.abstractmethod
    def _map_transaction(self, raw: dict) -> Transaction:
        raise NotImplementedError

    def _fetch_balance(self, api_key: str):
        """Current balance in the account's currency, or None if unsupported."""
        return None

    def _fetch_reconcile_balance(self, api_key: str):
        """Balance for the reconcile-only flow, without fetching transactions.
        Defaults to ``_fetch_balance`` (correct for investments, which already
        fetch their balance independently); overridden where _fetch_balance
        depends on records pulled during a transaction fetch."""
        return self._fetch_balance(api_key)

    def check_connection(self, api_key: str) -> dict:
        """Health info for the doctor command. Overridden per importer type."""
        return {}

    def _fetch_item(self, api_key: str, item_id: str) -> dict:
        """Fetch a Pluggy item (connection status, freshness, connector name)."""
        response = requests.get(
            f"{PLUGGY_API}/items/{item_id}", headers={"X-API-KEY": api_key}
        )
        return self._json_or_raise(response, f"{self.name} (item)")

    def _authenticate(self) -> str:
        response = requests.post(f"{PLUGGY_API}/auth", data={
            "clientId": self.pluggy_client_id,
            "clientSecret": self.pluggy_client_secret,
        })
        return response.json()['apiKey']

    def _json_or_raise(self, response, label: str) -> dict:
        """Surface Pluggy errors loudly instead of importing nothing on a bad id."""
        if not response.ok:
            detail = ''
            try:
                detail = response.json().get('message', '')
            except ValueError:
                detail = response.text[:120]
            raise RuntimeError(
                f"Pluggy returned HTTP {response.status_code} for {label} "
                f"(pluggy_source={self.pluggy_source!r}): {detail}"
            )
        return response.json()

    def _fetch_paginated(self, url: str, params: dict, api_key: str, label: str = None) -> list:
        """Fetch every page of a Pluggy list endpoint (not just the first).

        Pluggy caps a page at 500 results, so a single request silently
        truncates busy accounts. We loop until the reported ``totalPages``.
        """
        headers = {"X-API-KEY": api_key}
        label = label or self.name
        results = []
        page = 1
        while True:
            response = requests.get(
                url, params={**params, "page": page, "pageSize": PAGE_SIZE}, headers=headers
            )
            payload = self._json_or_raise(response, label)
            batch = payload.get('results', [])
            results.extend(batch)
            total_pages = payload.get('totalPages') or 1
            if self.debug:
                console.print(
                    f"  [muted]\\[debug] {escape(label)}: page {page}/{total_pages} "
                    f"(+{len(batch)}, fetched {len(results)}, Pluggy total {payload.get('total', '?')})[/]"
                )
            if page >= total_pages or not batch:
                break
            page += 1
        return results

    def _get_amount(self, transaction: dict) -> int:
        if transaction.get('amountInAccountCurrency') is not None:
            return int(transaction['amountInAccountCurrency'] * 1000)
        return int(transaction['amount'] * 1000)


class AccountTransactionsImporter(PluggyImporter):
    """Importer for BANK/CREDIT accounts.

    ``pluggy_source`` is a Pluggy *account* id; transactions come from the
    shared ``GET /transactions`` endpoint.
    """

    def _fetch_raw(self, api_key: str) -> list:
        # Validate the account up front. /transactions returns 200 + empty for an
        # unknown accountId, so without this a bad id would silently import nothing.
        self._account = self._fetch_account(api_key)
        return self._fetch_paginated(
            f"{PLUGGY_API}/transactions/",
            {"from": self.start_import_date, "accountId": self.pluggy_source},
            api_key,
        )

    def _fetch_account(self, api_key: str) -> dict:
        response = requests.get(
            f"{PLUGGY_API}/accounts/{self.pluggy_source}",
            headers={"X-API-KEY": api_key},
        )
        return self._json_or_raise(response, f"{self.name} (account)")

    def _fetch_balance(self, api_key: str):
        # Reuse the account record already fetched in _fetch_raw.
        return (getattr(self, '_account', None) or {}).get('balance')

    def _fetch_reconcile_balance(self, api_key: str):
        # No transaction fetch happened, so read the account record directly.
        self._account = self._fetch_account(api_key)
        return self._account.get('balance')

    def check_connection(self, api_key: str) -> dict:
        account = self._fetch_account(api_key)
        info = {
            'balance': account.get('balance'),
            'account_name': account.get('name'),
        }
        item_id = account.get('itemId')
        if item_id:
            item = self._fetch_item(api_key, item_id)
            info['status'] = item.get('status')
            info['last_updated'] = item.get('lastUpdatedAt')
            info['connector'] = (item.get('connector') or {}).get('name')
            info['ok'] = item.get('status') in ('UPDATED', 'UPDATING', None)
        return info
