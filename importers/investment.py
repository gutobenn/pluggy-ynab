from .transaction import Transaction
from .base import PluggyImporter, PLUGGY_API


class PluggyInvestmentData(PluggyImporter):
    """Report-only importer for investments.

    Pluggy has no INVESTMENT *account* type — investments are a separate
    resource and (for the brazilian connectors seen here) expose only a current
    *balance* per position, no transactions. So this importer imports nothing;
    it just sums the balances of the positions that match ``investment_filter``
    and exposes the total as ``pluggy_balance`` for the reconciliation report.
    The user adjusts YNAB manually from that comparison.

    ``pluggy_source`` is a Pluggy *item* id. ``investment_filter`` selects which
    of the item's positions belong to this YNAB account — AND-combined optional
    keys, each a single value or a list:
      - ``type``    e.g. "FIXED_INCOME" or ["EQUITY", "ETF", "MUTUAL_FUND"]
      - ``subtype`` e.g. "CDB"
      - ``rate``    e.g. 120 (Nubank Caixinha Turbo) or [100, 115]
    An empty/absent filter matches every position.
    """

    def _fetch_raw(self, api_key: str) -> list:
        return []  # investments expose no transactions; we report balance only

    def _fetch_balance(self, api_key: str):
        investments = self._fetch_paginated(
            f"{PLUGGY_API}/investments", {"itemId": self.pluggy_source}, api_key,
            label=f"{self.name} (investments)",
        )
        matching = [inv for inv in investments if self._matches(inv)]
        self.matched_count = len(matching)
        if self.debug:
            total = len(investments)
            print(f"  [debug] {self.name}: {self.matched_count}/{total} positions match {self.investment_filter}")
        return sum(inv.get('balance') or 0 for inv in matching)

    def _matches(self, investment: dict) -> bool:
        for key in ('type', 'subtype', 'rate'):
            if key not in self.investment_filter:
                continue
            wanted = self.investment_filter[key]
            allowed = wanted if isinstance(wanted, list) else [wanted]
            if investment.get(key) not in allowed:
                return False
        return True

    def _map_transaction(self, raw: dict) -> Transaction:  # never called (_fetch_raw is empty)
        raise NotImplementedError
