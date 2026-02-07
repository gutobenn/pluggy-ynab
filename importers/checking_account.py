from .transaction import Transaction
from .base import PluggyImporter


class PluggyCheckingAccountData(PluggyImporter):
    label = "CHECKING ACCOUNT TRANSACTIONS"

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
        description = account_transaction['description']
        category_id = account_transaction['categoryId']
        document_payees = self.mappings.get('document_payees', {})

        if description == 'Pagamento de fatura':
            return 'Nubank'

        if description.startswith('Transferência enviada'):
            parts = description.split('|')
            return parts[1] if len(parts) > 1 else description

        if description.startswith('Transferência Recebida'):
            payer_doc = None
            try:
                payer_doc = account_transaction['paymentData']['payer']['documentNumber']['value']
            except (TypeError, KeyError):
                pass

            if payer_doc and payer_doc in document_payees:
                return document_payees[payer_doc]

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
