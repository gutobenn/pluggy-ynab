# pluggy-ynab

Sincroniza transações bancárias brasileiras para o YNAB automaticamente, usando o [Pluggy](https://pluggy.ai/) como provedor de dados e o [ynab-sdk](https://github.com/andreroggeri/ynab-sdk-python) para importação.

## Como funciona

O script conecta na API do Pluggy para buscar transações de **múltiplas contas** — contas correntes, cartões de crédito e contas de investimento — de **vários bancos** (Nubank, Banco do Brasil, Itaú, BTG, etc.) e importa no YNAB via API.

As contas a sincronizar são declaradas em `accounts.json`; os segredos (token do YNAB e credenciais do Pluggy) ficam no `.env`.

## Instalação

```bash
python3 -m venv .venv
source .venv/bin/activate
pip install -r requirements.txt
```

## Configuração

### 1. Variáveis de ambiente (segredos)

Copie o arquivo de exemplo e preencha com suas credenciais:

```bash
cp .env.example .env
```

```env
YNAB_TOKEN=seu-token-ynab
YNAB_BUDGET=nome-do-orcamento
PLUGGY_CLIENT_ID=seu-pluggy-client-id
PLUGGY_CLIENT_SECRET=seu-pluggy-client-secret
```

### 2. Contas a sincronizar (`accounts.json`)

Copie o arquivo de exemplo e liste cada conta que quer sincronizar:

```bash
cp accounts.example.json accounts.json
```

Cada conta tem os campos:

- **bank**: identificador do banco (ex.: `nubank`, `banco_do_brasil`, `itau`, `btg`). As regras de limpeza de payee específicas do Nubank só rodam quando `bank` é `nubank`; os demais bancos usam a descrição crua + `mappings.json` (refinamos por banco depois, conforme surgem dados reais).
- **type**: `checking`, `credit_card` ou `investment`.
- **ynab_account**: nome exato da conta no YNAB (usado para localizar a conta destino).
- **pluggy_account_id**: id da conta no Pluggy — para `checking` e `credit_card`.
- **pluggy_item_id**: id do *item* no Pluggy — para `investment` (o Pluggy não tem "conta" de investimento; listamos os investimentos do item e agregamos as transações de cada um).
- **enabled** (opcional): `false` para pular a conta sem removê-la.

```json
{
  "accounts": [
    { "bank": "nubank", "type": "checking",    "ynab_account": "Nome da conta corrente no YNAB", "pluggy_account_id": "..." },
    { "bank": "nubank", "type": "credit_card", "ynab_account": "Nome do cartao no YNAB",          "pluggy_account_id": "..." },
    { "bank": "btg",    "type": "investment",  "ynab_account": "Nome do investimento no YNAB",    "pluggy_item_id": "...", "enabled": false }
  ]
}
```

> **Investimentos:** as transações são importadas com sinal por tipo de movimento — `BUY`/`TRANSFER` entram como crédito e `SELL`/`TAX` como débito na tracking account do YNAB. Confira na primeira sincronização e ajuste `OUTFLOW_TYPES` em `importers/investment.py` se necessário. Se a conta de investimento também estiver no YNAB junto da conta corrente, atenção para não contar aportes em dobro.

### 3. Mapeamentos personalizados (opcional)

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
