from .transaction import Transaction
from .base import PluggyImporter


class PluggyCreditCardData(PluggyImporter):
    label = "CREDIT CARD TRANSACTIONS"

    def _map_transaction(self, card_transaction: dict) -> Transaction:
        payee = memo = card_transaction['description']
        amount = self._get_amount(card_transaction)

        apple_subscriptions = {float(k): v for k, v in self.mappings.get('apple_subscriptions', {}).items()}
        ifood_restaurants = self.mappings.get('ifood_restaurants', {})

        if payee.startswith('Apple.Com/Bill') and apple_subscriptions:
            payee = apple_subscriptions.get(card_transaction['amount'], card_transaction['description'])
            memo = payee + ' (via Apple)'

        elif payee.startswith('Ifd*'):
            restaurant_name = ifood_restaurants.get(card_transaction['description'], card_transaction['description'].split('Ifd*')[1])
            payee = 'Ifood'
            memo = 'Ifood - ' + restaurant_name

        elif payee.startswith('Uber'):
            payee = 'Uber'
            memo = 'Uber - ' + card_transaction['date'][0:10] + ' ' + card_transaction['date'][12:16]

        elif payee.startswith('Comobi-Rs*'):
            payee = 'LIGA Coop'
            memo = 'LIGA Coop - ' + card_transaction['date'][0:10] + ' ' + card_transaction['date'][12:16]

        elif payee.startswith('Paypal *'):
            payee = card_transaction['description'].split('Paypal *')[1]
            memo = payee + ' (via Paypal)'

        elif payee.startswith('Mercadolivre*'):
            payee = 'Mercado Livre'
            memo = card_transaction['description'].split('Mercadolivre*')[1]

        elif payee.startswith('IOF de compra internacional'):
            payee = 'IOF de compra internacional'
            memo = '>>> IOF de compra próxima a R$' + str(round(card_transaction['amount'] / 3.38 * 100, 2))

        return {
            'transaction_id': card_transaction['id'],
            'account_id': self.account_id,
            'amount': amount * -1,
            'payee': payee,
            'date': card_transaction['date'][0:10],
            'memo': memo,
        }
