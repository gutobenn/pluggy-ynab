import abc
from typing import List

import requests

from .data_importer import DataImporter
from .transaction import Transaction


class PluggyImporter(DataImporter):
    label: str = ""

    def __init__(self, account_id: str, client_id: str, client_secret: str,
                 pluggy_account: str, start_import_date: str, mappings: dict = None):
        self.account_id = account_id
        self.pluggy_client_id = client_id
        self.pluggy_client_secret = client_secret
        self.pluggy_account = pluggy_account
        self.start_import_date = start_import_date
        self.mappings = mappings or {}

    def get_data(self) -> List[Transaction]:
        api_key = self._authenticate()
        raw_transactions = self._fetch_transactions(api_key)
        transactions = [self._map_transaction(t) for t in raw_transactions]
        self._print_transactions(transactions)
        return transactions

    @abc.abstractmethod
    def _map_transaction(self, raw: dict) -> Transaction:
        raise NotImplementedError

    def _authenticate(self) -> str:
        response = requests.post("https://api.pluggy.ai/auth", data={
            "clientId": self.pluggy_client_id,
            "clientSecret": self.pluggy_client_secret,
        })
        return response.json()['apiKey']

    def _fetch_transactions(self, api_key: str) -> list:
        response = requests.get("https://api.pluggy.ai/transactions/", params={
            "page": 1,
            "pageSize": 500,
            "from": self.start_import_date,
            "accountId": self.pluggy_account,
        }, headers={"X-API-KEY": api_key})
        return response.json()['results']

    def _get_amount(self, transaction: dict) -> int:
        if transaction['amountInAccountCurrency'] is not None:
            return int(transaction['amountInAccountCurrency'] * 1000)
        return int(transaction['amount'] * 1000)

    def _print_transactions(self, transactions: List[Transaction]):
        GREEN = "\033[92m"
        RED = "\033[91m"
        YELLOW = "\033[93m"
        RESET = "\033[0m"

        print()
        print(f"{YELLOW}=== {self.label} ==={RESET}")

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
