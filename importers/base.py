import abc
from typing import List

import requests

from .data_importer import DataImporter
from .transaction import Transaction

PLUGGY_API = "https://api.pluggy.ai"


class PluggyImporter(DataImporter):
    """Base class for any Pluggy-backed importer.

    Subclasses implement ``_fetch_raw`` (how to pull raw records from Pluggy)
    and ``_map_transaction`` (how to turn one raw record into a Transaction).
    """

    def __init__(self, *, name: str, bank: str, account_id: str,
                 client_id: str, client_secret: str, pluggy_source: str,
                 start_import_date: str, mappings: dict = None):
        self.name = name                       # YNAB account name, used as display label
        self.bank = bank                       # e.g. "nubank" — selects bank-specific parsing
        self.account_id = account_id           # YNAB account id (destination)
        self.pluggy_client_id = client_id
        self.pluggy_client_secret = client_secret
        self.pluggy_source = pluggy_source     # Pluggy account id (bank/credit) or item id (investment)
        self.start_import_date = start_import_date
        self.mappings = mappings or {}

    def get_data(self) -> List[Transaction]:
        api_key = self._authenticate()
        raw_transactions = self._fetch_raw(api_key)
        transactions = [self._map_transaction(t) for t in raw_transactions]
        self._print_transactions(transactions)
        return transactions

    @abc.abstractmethod
    def _fetch_raw(self, api_key: str) -> list:
        raise NotImplementedError

    @abc.abstractmethod
    def _map_transaction(self, raw: dict) -> Transaction:
        raise NotImplementedError

    def _authenticate(self) -> str:
        response = requests.post(f"{PLUGGY_API}/auth", data={
            "clientId": self.pluggy_client_id,
            "clientSecret": self.pluggy_client_secret,
        })
        return response.json()['apiKey']

    def _get_amount(self, transaction: dict) -> int:
        if transaction.get('amountInAccountCurrency') is not None:
            return int(transaction['amountInAccountCurrency'] * 1000)
        return int(transaction['amount'] * 1000)

    def _print_transactions(self, transactions: List[Transaction]):
        GREEN = "\033[92m"
        RED = "\033[91m"
        YELLOW = "\033[93m"
        RESET = "\033[0m"

        print()
        print(f"{YELLOW}=== {self.name} ({len(transactions)}) ==={RESET}")

        for transaction in reversed(transactions):
            date = transaction['date']
            amount = transaction['amount'] / 1000
            amount_str = f"${amount:.2f}" if amount >= 0 else f"-${abs(amount):.2f}"
            payee = transaction['payee']
            memo = transaction.get('memo', '')

            if amount >= 0:
                colored_type = f"{GREEN}CREDIT{RESET}"
            else:
                colored_type = f"{RED}DEBIT {RESET}"

            line = f"{date:<10} | {amount_str:>10} | {colored_type} | {payee}"
            if memo and memo != payee:
                line += f" | {memo}"
            print(line)


class AccountTransactionsImporter(PluggyImporter):
    """Importer for BANK/CREDIT accounts.

    ``pluggy_source`` is a Pluggy *account* id; transactions come from the
    shared ``GET /transactions`` endpoint.
    """

    def _fetch_raw(self, api_key: str) -> list:
        response = requests.get(f"{PLUGGY_API}/transactions/", params={
            "page": 1,
            "pageSize": 500,
            "from": self.start_import_date,
            "accountId": self.pluggy_source,
        }, headers={"X-API-KEY": api_key})
        return response.json().get('results', [])
