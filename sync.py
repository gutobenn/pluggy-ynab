import argparse
import json
import os
from concurrent.futures import ThreadPoolExecutor, as_completed
from datetime import datetime, timedelta

import requests
from dotenv import load_dotenv
from ynab_sdk import YNAB

from importers.base import PLUGGY_API, safe_print
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

# YNAB and Pluggy balances are compared to the cent; allow sub-cent rounding.
RECONCILE_TOLERANCE = 10  # milliunits (R$0,01)


def load_json(path, default=None):
    if os.path.exists(path):
        with open(path) as f:
            return json.load(f)
    return default


def money(milliunits):
    return f"{milliunits / 1000:.2f}"


def list_accounts(item_id):
    """Discover the Pluggy account ids under an item, to fill in accounts.json."""
    api_key = requests.post(f"{PLUGGY_API}/auth", data={
        "clientId": os.environ['PLUGGY_CLIENT_ID'],
        "clientSecret": os.environ['PLUGGY_CLIENT_SECRET'],
    }).json().get('apiKey')
    if not api_key:
        print(f"{RED}Pluggy authentication failed.{RESET} Check PLUGGY_CLIENT_ID / PLUGGY_CLIENT_SECRET in .env.")
        return

    headers = {"X-API-KEY": api_key}
    print(f"\n{BOLD}{BLUE}=== Pluggy accounts for item {item_id} ==={RESET}")

    response = requests.get(f"{PLUGGY_API}/accounts", params={"itemId": item_id}, headers=headers)
    if not response.ok:
        detail = response.json().get('message') if 'json' in response.headers.get('content-type', '') else response.text[:120]
        print(f"  {RED}HTTP {response.status_code}:{RESET} {detail}")
        print(f"  Check the item id at dashboard.pluggy.ai (it must belong to this Pluggy application).")
        return

    accounts = response.json().get('results', [])
    if not accounts:
        print(f"  {YELLOW}No accounts found.{RESET} Verify the item id and that it belongs to this Pluggy app.")
    for account in accounts:
        config_type = 'credit_card' if account.get('type') == 'CREDIT' else 'checking'
        print(f"  {GREEN}{account['id']}{RESET}  {account.get('type')}/{account.get('subtype')}  "
              f"balance={account.get('balance')}  name={account.get('name')!r}")
        print(f"      -> in accounts.json: \"type\": \"{config_type}\", \"pluggy_account_id\": \"{account['id']}\"")

    investments = requests.get(f"{PLUGGY_API}/investments", params={"itemId": item_id}, headers=headers)
    results = investments.json().get('results', []) if investments.ok else []
    if results:
        total = sum(i.get('balance') or 0 for i in results)
        print(f"\n  {BOLD}Investments:{RESET} {len(results)} found, total balance={total:.2f}")
        print(f"      -> for an investment account use the item id itself: "
              f"\"type\": \"investment\", \"pluggy_item_id\": \"{item_id}\"")


RECONCILE_SECTIONS = [
    ('checking', 'CHECKING'),
    ('credit_card', 'CREDIT CARDS'),
    ('investment', 'INVESTMENTS'),
]


def _print_recon_row(row, ynab_by_name):
    account = ynab_by_name.get(row['name'])
    if account is None:
        print(f"  {row['name']:<22}{RED}YNAB account not found{RESET}")
        return

    cleared = account.cleared_balance
    uncleared = account.uncleared_balance
    total = cleared + uncleared
    pluggy = row['pluggy_balance']

    if pluggy is None:
        print(f"  {row['name']:<22}{money(cleared):>13}{money(uncleared):>13}"
              f"{money(total):>13}{'?':>13}{'?':>11}  {YELLOW}no Pluggy balance{RESET}")
        return

    pluggy_milli = round(pluggy * 1000)
    # Credit cards: Pluggy reports the amount owed (positive); YNAB shows it negative.
    expected = -pluggy_milli if row['type'] == 'credit_card' else pluggy_milli
    diff = total - expected
    status = f"{GREEN}match{RESET}" if abs(diff) <= RECONCILE_TOLERANCE else f"{RED}MISMATCH{RESET}"
    if row['type'] == 'investment' and row.get('positions') is not None:
        status += f" {YELLOW}({row['positions']} pos.){RESET}"

    print(f"  {row['name']:<22}{money(cleared):>13}{money(uncleared):>13}"
          f"{money(total):>13}{money(pluggy_milli):>13}{money(diff):>11}  {status}")


def print_reconciliation(rows, ynab_by_name):
    """Compare each account's Pluggy balance against its YNAB balance
    (cleared + uncleared = total), grouped by type, flagging match/mismatch."""
    print(f"\n{BOLD}{BLUE}=== BALANCE RECONCILIATION ==={RESET}")
    header = (f"  {BOLD}{'Account':<22}{'YNAB clr':>13}{'YNAB unclr':>13}"
              f"{'YNAB total':>13}{'Pluggy':>13}{'Diff':>11}{RESET}  Status")

    by_type = {}
    for row in rows:
        by_type.setdefault(row['type'], []).append(row)
    # known sections first, then any unexpected types
    ordered = RECONCILE_SECTIONS + [(t, t.upper()) for t in by_type if t not in dict(RECONCILE_SECTIONS)]

    for type_key, title in ordered:
        group = by_type.get(type_key)
        if not group:
            continue
        print(f"\n  {BOLD}{title}{RESET}")
        print(header)
        for row in group:
            _print_recon_row(row, ynab_by_name)

    print(f"\n  {YELLOW}Note:{RESET} compares Pluggy's current balance to YNAB (cleared + uncleared). "
          f"Credit cards are sign-inverted. Investments are report-only unless you pass "
          f"{BOLD}--update-investments{RESET}, which posts the difference as a 'Rendimento' transaction. "
          f"A mismatch can also mean missing/extra transactions or history older than --from not in YNAB.")


def main():
    load_dotenv()

    parser = argparse.ArgumentParser(description='Sync transactions from Pluggy to YNAB')
    parser.add_argument('--from', dest='start_date', help='Start date for import (YYYY-MM-DD). Defaults to 14 days ago.')
    parser.add_argument('-n', '--dry-run', action='store_true',
                        help="Fetch and print everything, but don't save to YNAB.")
    parser.add_argument('--debug', action='store_true',
                        help='Verbose output: per-page fetch counts and Pluggy totals.')
    parser.add_argument('--list-accounts', metavar='ITEM_ID',
                        help='List the Pluggy accounts/investments under an item id (to fill accounts.json) and exit.')
    parser.add_argument('--update-investments', action='store_true',
                        help="For investment accounts, post the YNAB↔Pluggy difference as a 'Rendimento' transaction so the tracking account catches up to the current balance.")
    args = parser.parse_args()

    if args.list_accounts:
        list_accounts(args.list_accounts)
        return

    debug = args.debug or args.dry_run

    now = datetime.now()
    today = now.strftime('%Y-%m-%d')
    default_date = (now - timedelta(days=14)).strftime('%Y-%m-%d')
    start_import_date = args.start_date or default_date

    base_dir = os.path.dirname(__file__)
    mappings = load_json(os.path.join(base_dir, 'mappings.json'), {})

    accounts_config = load_json(os.path.join(base_dir, 'accounts.json'))
    if not accounts_config or not accounts_config.get('accounts'):
        print(f"{RED}No accounts configured.{RESET} Copy accounts.example.json to accounts.json and fill it in.")
        return

    client_id = os.environ['PLUGGY_CLIENT_ID']
    client_secret = os.environ['PLUGGY_CLIENT_SECRET']

    print(f"{BOLD}{BLUE}pluggy-ynab{RESET} — importing transactions since {BOLD}{start_import_date}{RESET}")
    print("Connecting to YNAB (loading budget and accounts)...")
    ynab = YNAB(os.environ['YNAB_TOKEN'])
    budget = find_by_name(ynab.budgets.get_budgets().data.budgets, os.environ['YNAB_BUDGET'])
    ynab_accounts = ynab.accounts.get_accounts(budget.id).data.accounts

    ynab_importer = YNABTransactionImporter(ynab, budget.id, start_import_date)

    skipped = []

    # Build an importer per account first (cheap, no network here).
    tasks = []  # list of (entry, importer, ynab_account)
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
                debug=debug,
                investment_filter=entry.get('investment_filter'),
            )
        except Exception as e:
            print(f"{RED}Failed to set up '{label}':{RESET} {e}")
            skipped.append(label)
            continue

        tasks.append((entry, importer, ynab_account))

    # Fetch every account from Pluggy in parallel — the run is network-bound, so
    # concurrent requests cut wall-clock time roughly to the slowest single account.
    # Print a live counter as each finishes so the (otherwise blank) wait shows progress.
    results = {}  # importer -> list[Transaction]
    if tasks:
        total = len(tasks)
        print(f"\n{BOLD}{BLUE}Fetching {total} account(s) from Pluggy...{RESET}")
        with ThreadPoolExecutor(max_workers=min(total, 8)) as pool:
            futures = {pool.submit(importer.get_data): (entry, importer)
                       for entry, importer, _ in tasks}
            for done, future in enumerate(as_completed(futures), start=1):
                entry, importer = futures[future]
                name = entry.get('ynab_account', '?')
                try:
                    transactions = future.result()
                    results[importer] = transactions
                    safe_print(f"  {GREEN}✓{RESET} [{done}/{total}] {name} "
                               f"{YELLOW}({len(transactions)} fetched){RESET}")
                except Exception as e:
                    safe_print(f"  {RED}✗ [{done}/{total}] {name}: {e}{RESET}")

    # Process results on the main thread, in config order, so the shared
    # transaction list and YNAB writes stay race-free.
    reconciliations = []
    for entry, importer, ynab_account in tasks:
        if importer not in results:  # fetch failed and was already reported above
            skipped.append(entry.get('ynab_account', '?'))
            continue

        importer.print_transactions(results[importer])
        ynab_importer.add_transactions(results[importer])
        reconciliations.append({
            'name': entry['ynab_account'],
            'type': entry['type'],
            'pluggy_balance': importer.pluggy_balance,
            'positions': getattr(importer, 'matched_count', None),
        })

        # Optionally true up investment tracking accounts to the live balance.
        if args.update_investments and entry['type'] == 'investment' and importer.pluggy_balance is not None:
            diff = round(importer.pluggy_balance * 1000) - ynab_account.balance
            if abs(diff) > RECONCILE_TOLERANCE:
                ynab_importer.add_adjustment(
                    account_id=ynab_account.id,
                    amount=diff,
                    date=today,
                    payee="Rendimento",
                    memo="Rendimento",
                    import_id=f"REND-{today}-{ynab_account.id[:8]}",
                )
                print(f"  {GREEN}Rendimento{RESET} {entry['ynab_account']}: {money(diff)}")

    print(f"\n{BOLD}{BLUE}=== IMPORT SUMMARY ==={RESET}")
    if args.dry_run:
        print(f"  {YELLOW}DRY RUN — nothing saved to YNAB.{RESET}")
        print(f"  Transactions that would be imported: {len(ynab_importer.transactions)}")
    elif not ynab_importer.transactions:
        print(f"  {YELLOW}Nothing to import.{RESET}")
    else:
        response = ynab_importer.save()
        if 'error' in response:
            err = response['error']
            print(f"  {RED}YNAB API error:{RESET} {err.get('name')} - {err.get('detail')}")
        else:
            print(f"  {GREEN}+ New transactions imported:{RESET} {len(response['data']['transaction_ids'])}")
            print(f"  {YELLOW}= Duplicate transactions:{RESET} {len(response['data']['duplicate_import_ids'])}")

    if skipped:
        print(f"  {RED}! Accounts skipped/failed:{RESET} {', '.join(skipped)}")

    if reconciliations:
        # Re-fetch so YNAB balances reflect any transactions just imported.
        fresh = ynab.accounts.get_accounts(budget.id).data.accounts
        print_reconciliation(reconciliations, {a.name: a for a in fresh})


if __name__ == '__main__':
    main()
