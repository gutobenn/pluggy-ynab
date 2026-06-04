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

### 2. Conectando os bancos no Pluggy e obtendo os IDs

O Pluggy é uma plataforma para desenvolvedores — **não existe um botão "adicionar banco" no painel**. Cada conexão com um banco é um *item*, criado pelo fluxo do **Pluggy Connect**. Para uso pessoal, o caminho mais simples (e grátis) é o **app Demo** da sua aplicação, combinado com o **MeuPluggy** quando o conector do banco real não estiver disponível.

**a) Conectar um banco**

1. Acesse **dashboard.pluggy.ai** e faça login.
2. Menu lateral → **Applications / Aplicações** → abra a sua aplicação (a mesma do `PLUGGY_CLIENT_ID` do `.env`).
3. Na página da aplicação, clique em **"Ir para Demo"** — abre o **demo.pluggy.ai** já vinculado às credenciais da sua app. (Abra sempre por aqui, e não direto pelo `demo.pluggy.ai`, para o item ficar na mesma aplicação do `.env`.)
4. No app Demo, clique em **conectar conta** → o widget do **Pluggy Connect** abre → escolha a instituição → faça o login/consentimento do Open Finance. Isso cria o *item*.
   - Se um banco real (BB/Itaú/BTG) não aparecer ou for bloqueado (comum em apps de trial), conecte-o primeiro em **meu.pluggy.ai**, habilite o conector **MeuPluggy** na lista de conectores da sua app e autorize-o no app Demo — **uma vez por banco**.

**b) Copiar o item id**

5. No app Demo, canto superior direito → menu de **três pontos (⋮)** → **"Copiar Item ID"**.

**c) Descobrir os `pluggy_account_id`**

6. Liste as contas daquele item:
   ```bash
   python sync.py --list-accounts <ITEM_ID>
   ```
   Imprime cada conta (id, tipo, saldo, nome) com o trecho pronto para colar no `accounts.json`, e conta os investimentos do item. Para contas `checking`/`credit_card` use o `pluggy_account_id` impresso; para `investment` use o próprio item id como `pluggy_item_id`.

### 3. Contas a sincronizar (`accounts.json`)

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

### 4. Mapeamentos personalizados (opcional)

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
python sync.py                      # importa os últimos 30 dias
python sync.py --from 2026-01-01    # importa a partir de uma data
```

Todas as páginas de transações são buscadas (o Pluggy limita a 500 por página), então contas movimentadas não são truncadas.

### Verificando / depurando

```bash
python sync.py --dry-run            # busca e mostra tudo, mas NÃO grava no YNAB
python sync.py --dry-run --debug    # idem, com contagem por página vs. total do Pluggy
python sync.py --dry-run --from 2020-01-01   # puxa o máximo de histórico para conferir
```

- **`--dry-run` (`-n`)**: roda o fluxo inteiro sem salvar no YNAB. Útil para conferir se as transações estão vindo corretas antes de gravar.
- **`--debug`**: imprime, por página, quantas transações vieram e o total que o Pluggy reporta — se `fetched == total`, você pegou tudo.

### Conferência de saldos (reconciliação)

Ao final de cada execução é impressa uma tabela comparando, por conta, o **saldo atual no Pluggy** com o **saldo no YNAB** (`cleared + uncleared = total`), sinalizando `match` ou `MISMATCH`. Cartões de crédito são comparados com sinal invertido (Pluggy reporta o valor devido como positivo; o YNAB mostra negativo). Um `MISMATCH` indica transações faltando/sobrando — ou histórico anterior ao `--from` que não está no YNAB.
