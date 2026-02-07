# pluggy-ynab

Sincroniza transações bancárias brasileiras para o YNAB automaticamente, usando o [Pluggy](https://pluggy.ai/) como provedor de dados e o [ynab-sdk](https://github.com/andreroggeri/ynab-sdk-python) para importação.

## Como funciona

O script conecta na API do Pluggy para buscar transações de conta corrente e cartão de crédito, e importa no YNAB via API.

## Instalação

```bash
python3 -m venv .venv
source .venv/bin/activate
pip install -r requirements.txt
```

## Configuração

### 1. Variáveis de ambiente

Copie o arquivo de exemplo e preencha com seus dados:

```bash
cp .env.example .env
```

Edite o `.env` com suas credenciais:

```env
YNAB_TOKEN=seu-token-ynab
YNAB_BUDGET=nome-do-orcamento
CARD_ACCOUNT=nome-da-conta-cartao-no-ynab
CHECKING_ACCOUNT=nome-da-conta-corrente-no-ynab
PLUGGY_CLIENT_ID=seu-pluggy-client-id
PLUGGY_CLIENT_SECRET=seu-pluggy-client-secret
PLUGGY_CARD_ACCOUNT=id-da-conta-cartao-no-pluggy
PLUGGY_CHECKING_ACCOUNT=id-da-conta-corrente-no-pluggy
```

### 2. Mapeamentos personalizados (opcional)

Copie o arquivo de exemplo e personalize com suas assinaturas, restaurantes, etc:

```bash
cp mappings.example.json mappings.json
```

O arquivo `mappings.json` permite mapear:

- **apple_subscriptions**: Mapeamento de valor (R$) para nome da assinatura Apple
- **ifood_restaurants**: Mapeamento da descrição do iFood para nome do restaurante
- **document_payees**: Mapeamento de CNPJ para nome do pagador (ex: empregador)

Exemplo:

```json
{
  "apple_subscriptions": {
    "9.99": "Apple Music"
  },
  "ifood_restaurants": {
    "Ifd*Nome Do Restaurante": "Nome Amigável"
  },
  "document_payees": {
    "00.000.000/0001-00": "Nome da Empresa"
  }
}
```

## Sincronizando

```bash
python sync.py
```
