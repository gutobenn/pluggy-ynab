import argparse
import json
import os
from datetime import datetime, timedelta

from dotenv import load_dotenv
from ynab_sdk import YNAB

from importers.checking_account import PluggyCheckingAccountData
from importers.credit_card import PluggyCreditCardData
from importers.util import find_by_name
from ynab_importer import YNABTransactionImporter


def main():
    load_dotenv()

    parser = argparse.ArgumentParser(description='Sync transactions from Pluggy to YNAB')
    parser.add_argument('--from', dest='start_date', help='Start date for import (YYYY-MM-DD). Defaults to 30 days ago.')
    args = parser.parse_args()

    default_date = (datetime.now() - timedelta(days=30)).strftime('%Y-%m-%d')
    start_import_date = args.start_date or default_date

    mappings = {}
    mappings_path = os.path.join(os.path.dirname(__file__), 'mappings.json')
    if os.path.exists(mappings_path):
        with open(mappings_path) as f:
            mappings = json.load(f)

    ynab = YNAB(os.environ['YNAB_TOKEN'])

    budget = find_by_name(ynab.budgets.get_budgets().data.budgets, os.environ['YNAB_BUDGET'])
    ynab_accounts = ynab.accounts.get_accounts(budget.id).data.accounts

    ynab_importer = YNABTransactionImporter(ynab, budget.id, start_import_date)

    if os.environ.get('CARD_ACCOUNT'):
        account = find_by_name(ynab_accounts, os.environ['CARD_ACCOUNT'])
        pluggy_card_data = PluggyCreditCardData(
            account.id,
            os.environ['PLUGGY_CLIENT_ID'],
            os.environ['PLUGGY_CLIENT_SECRET'],
            os.environ['PLUGGY_CARD_ACCOUNT'],
            start_import_date,
            mappings,
        )
        ynab_importer.get_transactions_from(pluggy_card_data)

    if os.environ.get('CHECKING_ACCOUNT'):
        account = find_by_name(ynab_accounts, os.environ['CHECKING_ACCOUNT'])
        pluggy_checking_data = PluggyCheckingAccountData(
            account.id,
            os.environ['PLUGGY_CLIENT_ID'],
            os.environ['PLUGGY_CLIENT_SECRET'],
            os.environ['PLUGGY_CHECKING_ACCOUNT'],
            start_import_date,
            mappings,
        )
        ynab_importer.get_transactions_from(pluggy_checking_data)

    response = ynab_importer.save()

    GREEN = "\033[92m"
    YELLOW = "\033[93m"
    BLUE = "\033[94m"
    BOLD = "\033[1m"
    RESET = "\033[0m"

    print(f"\n{BOLD}{BLUE}=== IMPORT SUMMARY ==={RESET}")
    print(f"{BOLD}Imported transactions:{RESET}")
    print(f"  {GREEN}+ New transactions imported:{RESET} {len(response['data']['transaction_ids'])}")
    print(f"  {YELLOW}= Duplicate transactions:{RESET} {len(response['data']['duplicate_import_ids'])}")


if __name__ == '__main__':
    main()
