import argparse
import json
import os
from datetime import datetime, timedelta

from dotenv import load_dotenv
from ynab_sdk import YNAB

from importers.checking_account import PluggyCheckingAccountData
from importers.credit_card import PluggyCreditCardData
from importers.investment import PluggyInvestmentData
from importers.util import find_by_name
from ynab_importer import YNABTransactionImporter

# Maps the "type" field in accounts.json to the importer class.
IMPORTERS = {
    'checking': PluggyCheckingAccountData,
    'credit_card': PluggyCreditCardData,
    'investment': PluggyInvestmentData,
}

GREEN = "\033[92m"
YELLOW = "\033[93m"
BLUE = "\033[94m"
RED = "\033[91m"
BOLD = "\033[1m"
RESET = "\033[0m"


def load_json(path, default=None):
    if os.path.exists(path):
        with open(path) as f:
            return json.load(f)
    return default


def main():
    load_dotenv()

    parser = argparse.ArgumentParser(description='Sync transactions from Pluggy to YNAB')
    parser.add_argument('--from', dest='start_date', help='Start date for import (YYYY-MM-DD). Defaults to 30 days ago.')
    args = parser.parse_args()

    default_date = (datetime.now() - timedelta(days=30)).strftime('%Y-%m-%d')
    start_import_date = args.start_date or default_date

    base_dir = os.path.dirname(__file__)
    mappings = load_json(os.path.join(base_dir, 'mappings.json'), {})

    accounts_config = load_json(os.path.join(base_dir, 'accounts.json'))
    if not accounts_config or not accounts_config.get('accounts'):
        print(f"{RED}No accounts configured.{RESET} Copy accounts.example.json to accounts.json and fill it in.")
        return

    client_id = os.environ['PLUGGY_CLIENT_ID']
    client_secret = os.environ['PLUGGY_CLIENT_SECRET']

    ynab = YNAB(os.environ['YNAB_TOKEN'])
    budget = find_by_name(ynab.budgets.get_budgets().data.budgets, os.environ['YNAB_BUDGET'])
    ynab_accounts = ynab.accounts.get_accounts(budget.id).data.accounts

    ynab_importer = YNABTransactionImporter(ynab, budget.id, start_import_date)

    skipped = []
    for entry in accounts_config['accounts']:
        label = entry.get('ynab_account', '?')

        if entry.get('enabled') is False:
            continue

        importer_cls = IMPORTERS.get(entry.get('type'))
        if importer_cls is None:
            print(f"{RED}Skipping '{label}': unknown type '{entry.get('type')}'.{RESET}")
            skipped.append(label)
            continue

        try:
            ynab_account = find_by_name(ynab_accounts, entry['ynab_account'])
            importer = importer_cls(
                name=entry['ynab_account'],
                bank=entry.get('bank', ''),
                account_id=ynab_account.id,
                client_id=client_id,
                client_secret=client_secret,
                pluggy_source=entry.get('pluggy_account_id') or entry.get('pluggy_item_id'),
                start_import_date=start_import_date,
                mappings=mappings,
            )
            ynab_importer.get_transactions_from(importer)
        except Exception as e:
            print(f"{RED}Failed to import '{label}':{RESET} {e}")
            skipped.append(label)

    print(f"\n{BOLD}{BLUE}=== IMPORT SUMMARY ==={RESET}")

    if not ynab_importer.transactions:
        print(f"  {YELLOW}Nothing to import.{RESET}")
        if skipped:
            print(f"  {RED}! Accounts skipped/failed:{RESET} {', '.join(skipped)}")
        return

    response = ynab_importer.save()
    if 'error' in response:
        err = response['error']
        print(f"  {RED}YNAB API error:{RESET} {err.get('name')} - {err.get('detail')}")
        return

    print(f"{BOLD}Imported transactions:{RESET}")
    print(f"  {GREEN}+ New transactions imported:{RESET} {len(response['data']['transaction_ids'])}")
    print(f"  {YELLOW}= Duplicate transactions:{RESET} {len(response['data']['duplicate_import_ids'])}")
    if skipped:
        print(f"  {RED}! Accounts skipped/failed:{RESET} {', '.join(skipped)}")


if __name__ == '__main__':
    main()
