from .transaction import Transaction
from .base import AccountTransactionsImporter


class PluggyCheckingAccountData(AccountTransactionsImporter):
    def _map_transaction(self, account_transaction: dict) -> Transaction:
        payee = self._get_payee(account_transaction)
        amount = self._get_amount(account_transaction)

        return {
            'transaction_id': account_transaction['id'],
            'account_id': self.account_id,
            'amount': amount,
            'payee': payee,
            'date': account_transaction['date'][0:10],
            'memo': payee,
        }

    def _get_payee(self, account_transaction: dict) -> str:
        # Generic, cross-bank: map by the counterparty's document (CPF/CNPJ) when known.
        document_payees = self.mappings.get('document_payees', {})
        document = self._counterparty_document(account_transaction)
        if document and document in document_payees:
            return document_payees[document]

        if self.bank == 'nubank':
            return self._nubank_payee(account_transaction)

        return account_transaction['description']

    def _counterparty_document(self, account_transaction: dict) -> str:
        """CPF/CNPJ of the other party, from payer (incoming) or receiver (outgoing)."""
        payment_data = account_transaction.get('paymentData') or {}
        for side in ('payer', 'receiver'):
            try:
                return payment_data[side]['documentNumber']['value']
            except (TypeError, KeyError):
                continue
        return None

    def _nubank_payee(self, account_transaction: dict) -> str:
        description = account_transaction['description']
        category_id = account_transaction.get('categoryId')

        if description == 'Pagamento de fatura':
            return 'Nubank'

        if description.startswith('Transferência enviada'):
            parts = description.split('|')
            return parts[1] if len(parts) > 1 else description

        if description.startswith('Transferência Recebida'):
            parts = description.split('|')
            if len(parts) > 1:
                return parts[1]
            if category_id == '05000000':
                return 'Estorno de compra no débito'
            return description

        if description.startswith('Compra no débito'):
            parts = description.split('|')
            return parts[1] if len(parts) > 1 else description

        return description
