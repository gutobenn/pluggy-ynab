from datetime import datetime
from typing import List

import requests
from ynab_sdk import YNAB
from ynab_sdk.api.models.requests.transaction import TransactionRequest

from importers.data_importer import DataImporter
from importers.transaction import Transaction

# YNAB's bulk PATCH accepts an array of transactions; chunk well under any cap.
YNAB_PATCH_CHUNK = 1000


class YNABTransactionImporter:
    def __init__(self, ynab: YNAB, budget_id: str, starting_date: str):
        self.ynab = ynab
        self.budget_id = budget_id
        self.starting_date = datetime.strptime(starting_date, '%Y-%m-%d')
        self.transactions: List[TransactionRequest] = []

    def get_transactions_from(self, transaction_importer: DataImporter):
        return self.add_transactions(transaction_importer.get_data())

    def add_transactions(self, transactions: List[Transaction]):
        """Filter already-fetched transactions by date and queue them for import.

        Kept separate from the network fetch so accounts can be fetched
        concurrently while this (shared-state) step runs on the main thread.
        """
        transactions = filter(self._filter_transaction, transactions)
        transformed = map(self._create_transaction_request, transactions)
        self.transactions.extend(transformed)
        return self

    def save(self):
        return self.ynab.transactions.create_transactions(self.budget_id, self.transactions)

    def add_adjustment(self, account_id: str, amount: int, date: str,
                       payee: str, memo: str, import_id: str):
        """Append a synthetic balance-adjustment transaction (e.g. investment yield).
        ``amount`` is in milliunits; ``import_id`` keeps it idempotent per run."""
        self.transactions.append(TransactionRequest(
            account_id,
            date,
            amount,
            payee_name=payee[:50],
            import_id=import_id[:36],
            memo=memo[:200],
        ))

    def add_transfer(self, from_account_id: str, to_transfer_payee_id: str,
                     amount: int, date: str, memo: str, import_id: str):
        """Queue ONE leg of a self-transfer. YNAB auto-creates the linked mirror
        in the receiving account from ``to_transfer_payee_id``, so only the
        sending leg is posted. ``amount`` is the sender's signed milliunits
        (negative outflow); ``import_id`` (the sender leg's Pluggy id) keeps it
        idempotent across the overlapping import windows."""
        self.transactions.append(TransactionRequest(
            from_account_id,
            date,
            amount,
            payee_id=to_transfer_payee_id,   # NOT payee_name — this is what makes it a transfer
            import_id=import_id[:36],
            memo=memo[:200],
        ))

    def eligible(self, transactions: List[Transaction]) -> List[Transaction]:
        """The subset of ``transactions`` inside the import date window (the same
        filter ``add_transactions`` applies). Transfer dedup uses this so pairing
        only ever considers transactions that would actually be imported."""
        return [t for t in transactions if self._filter_transaction(t)]

    # ----- Reconcile (mark cleared → reconciled via the bulk PATCH endpoint) ----- #

    def _auth_headers(self) -> dict:
        return {
            "Authorization": f"Bearer {self.ynab.client.config.api_key}",
            "accept": "application/json",
        }

    def fetch_cleared_transaction_ids(self, account_id: str) -> List[str]:
        """IDs of this account's transactions that are currently 'cleared' (and
        not deleted) — exactly the ones a reconcile should lock."""
        response = self.ynab.transactions.get_transactions_from_account(self.budget_id, account_id)
        return [t.id for t in response.data.transactions
                if t.cleared == 'cleared' and not t.deleted]

    def reconcile_transactions(self, transaction_ids: List[str]) -> dict:
        """Bulk-mark the given transactions cleared → reconciled. The SDK has no
        PATCH helper, so call the YNAB bulk-update endpoint directly, reusing the
        SDK's token/host. Chunked. Returns ``{'reconciled': n}`` or, on failure,
        ``{'error': <detail>, 'reconciled': <done so far>}``."""
        reconciled = 0
        url = f"{self.ynab.client.config.full_url}/budgets/{self.budget_id}/transactions"
        for start in range(0, len(transaction_ids), YNAB_PATCH_CHUNK):
            chunk = transaction_ids[start:start + YNAB_PATCH_CHUNK]
            payload = {"transactions": [{"id": tid, "cleared": "reconciled"} for tid in chunk]}
            response = requests.patch(url, json=payload, headers=self._auth_headers())
            body = response.json() if response.content else {}
            if not response.ok or 'error' in body:
                error = body.get('error') or {}
                name = error.get('name') or f"HTTP {response.status_code}"
                return {'error': f"{name} - {error.get('detail', response.text[:200])}",
                        'reconciled': reconciled}
            reconciled += len(body.get('data', {}).get('transaction_ids', chunk))
        return {'reconciled': reconciled}

    def _create_transaction_request(self, transaction: Transaction) -> TransactionRequest:
        return TransactionRequest(
            transaction['account_id'],
            transaction['date'],
            transaction['amount'],
            payee_name=transaction['payee'][:50],
            import_id=transaction['transaction_id'],
            memo=transaction['memo'][:200],
        )

    def _filter_transaction(self, transaction: Transaction) -> bool:
        transaction_date = datetime.strptime(transaction['date'], '%Y-%m-%d')
        today = datetime.now().replace(hour=0, minute=0, second=0, microsecond=0)
        return self.starting_date <= transaction_date <= today
