import hashlib

from .transaction import Transaction
from .base import PluggyImporter, PLUGGY_API


class PluggyInvestmentData(PluggyImporter):
    """Imports investment movements (aportes, resgates, dividendos, ...).

    Pluggy has no INVESTMENT *account* type: investments are a separate
    resource. ``pluggy_source`` is therefore a Pluggy *item* id. We list the
    item's investments and aggregate the transactions of each one into the
    single YNAB (tracking) account.

    Sign convention (verify against real data, then tweak ``OUTFLOW_TYPES``):
    a movement that *reduces* the account value (SELL, TAX) is an outflow;
    everything else (BUY, TRANSFER, ...) is an inflow.
    """

    OUTFLOW_TYPES = {'SELL', 'TAX'}

    def _fetch_raw(self, api_key: str) -> list:
        self._investments = self._fetch_paginated(
            f"{PLUGGY_API}/investments", {"itemId": self.pluggy_source}, api_key,
            label=f"{self.name} (investments)",
        )

        raw = []
        for investment in self._investments:
            name = investment.get('name') or investment.get('type') or 'Investimento'
            transactions = self._fetch_paginated(
                f"{PLUGGY_API}/investments/{investment['id']}/transactions",
                {"from": self.start_import_date}, api_key,
                label=f"{self.name} / {name}",
            )
            for transaction in transactions:
                transaction['_investment_id'] = investment['id']
                transaction['_investment_name'] = name
                raw.append(transaction)
        return raw

    def _fetch_balance(self, api_key: str):
        # Sum of the item's investment balances (populated by _fetch_raw).
        return sum(inv.get('balance') or 0 for inv in getattr(self, '_investments', []))

    def _map_transaction(self, transaction: dict) -> Transaction:
        movement_type = transaction.get('type') or ''
        magnitude = int(round(abs(transaction['amount']) * 1000))
        amount = -magnitude if movement_type in self.OUTFLOW_TYPES else magnitude

        investment_name = transaction.get('_investment_name', 'Investimento')
        description = transaction.get('description') or movement_type or investment_name

        return {
            'transaction_id': self._import_id(transaction),
            'account_id': self.account_id,
            'amount': amount,
            'payee': investment_name,
            'date': transaction['date'][0:10],
            'memo': f'{movement_type} - {description}'.strip(' -'),
        }

    def _import_id(self, transaction: dict) -> str:
        """Stable YNAB import id (max 36 chars). Investment transactions don't
        always carry a Pluggy id, so fall back to a hash of their fields."""
        if transaction.get('id'):
            return str(transaction['id'])
        seed = '|'.join(str(transaction.get(k, '')) for k in
                        ('_investment_id', 'date', 'type', 'amount', 'quantity', 'description'))
        return 'INV-' + hashlib.md5(seed.encode()).hexdigest()[:28]
