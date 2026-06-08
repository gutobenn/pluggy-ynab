from typing import NotRequired, TypedDict


class Transaction(TypedDict):
    transaction_id: str
    account_id: str
    payee: str
    amount: int
    date: str
    memo: str
    # CPF/CNPJ of the other party, when Pluggy exposes it (checking accounts only).
    # Used by transfer dedup to confirm a candidate pair; absent on other importers.
    counterparty_document: NotRequired[str]
