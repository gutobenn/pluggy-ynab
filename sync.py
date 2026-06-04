import argparse
import json
import os
import sys
from concurrent.futures import ThreadPoolExecutor, as_completed
from datetime import datetime, timedelta

import requests
from dotenv import load_dotenv
from ynab_sdk import YNAB

import ui
from ui import console
from importers.base import PLUGGY_API
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

MAX_WORKERS = 8


def load_json(path, default=None):
    if os.path.exists(path):
        with open(path) as f:
            return json.load(f)
    return default


def default_start_date() -> str:
    return (datetime.now() - timedelta(days=14)).strftime('%Y-%m-%d')


class Session:
    """Holds the credentials and config shared across menu iterations, plus a
    lazily-connected YNAB client so it's only set up once per process."""

    def __init__(self):
        self.client_id = os.environ['PLUGGY_CLIENT_ID']
        self.client_secret = os.environ['PLUGGY_CLIENT_SECRET']
        base_dir = os.path.dirname(__file__)
        self.mappings = load_json(os.path.join(base_dir, 'mappings.json'), {})
        self.accounts_config = load_json(os.path.join(base_dir, 'accounts.json'))
        self.ynab = None
        self.budget = None
        self.ynab_accounts = None

    def connect_ynab(self):
        if self.ynab is None:
            with console.status("Connecting to YNAB (loading budget and accounts)…"):
                self.ynab = YNAB(os.environ['YNAB_TOKEN'])
                self.budget = find_by_name(
                    self.ynab.budgets.get_budgets().data.budgets, os.environ['YNAB_BUDGET']
                )
                self.ynab_accounts = self.ynab.accounts.get_accounts(self.budget.id).data.accounts
        return self

    def refresh_ynab_accounts(self):
        """Re-fetch balances so they reflect transactions just imported."""
        return self.ynab.accounts.get_accounts(self.budget.id).data.accounts

    def has_accounts(self) -> bool:
        if not self.accounts_config or not self.accounts_config.get('accounts'):
            ui.error("No accounts configured. Copy accounts.example.json to accounts.json and fill it in.")
            return False
        return True

    def account_labels(self):
        return [entry.get('ynab_account', '?') for entry in self.accounts_config['accounts']
                if entry.get('enabled') is not False]

    def investment_labels(self):
        return [entry.get('ynab_account', '?') for entry in self.accounts_config['accounts']
                if entry.get('enabled') is not False and entry.get('type') == 'investment']


# --------------------------------------------------------------------------- #
# Importer construction + concurrent fetch
# --------------------------------------------------------------------------- #

def build_importers(session, selected_labels, start_import_date, debug, *, require_ynab):
    """Build (entry, importer, ynab_account) per enabled (and selected) account.

    ``require_ynab=False`` (doctor) keeps an account even when its YNAB account
    is missing, so the connection can still be probed; ``ynab_account`` is then
    None. Returns ``(tasks, skipped)``."""
    tasks = []
    skipped = []
    selected = set(selected_labels) if selected_labels is not None else None

    for entry in session.accounts_config['accounts']:
        label = entry.get('ynab_account', '?')
        if entry.get('enabled') is False:
            continue
        if selected is not None and label not in selected:
            continue

        importer_cls = IMPORTERS.get(entry.get('type'))
        if importer_cls is None:
            ui.error(f"Skipping '{label}': unknown type '{entry.get('type')}'.")
            skipped.append(label)
            continue

        try:
            ynab_account = find_by_name(session.ynab_accounts, entry.get('ynab_account'))
        except ValueError:
            if require_ynab:
                ui.error(f"Failed to set up '{label}': YNAB account not found")
                skipped.append(label)
                continue
            ynab_account = None

        try:
            importer = importer_cls(
                name=label,
                bank=entry.get('bank', ''),
                account_id=ynab_account.id if ynab_account else '',
                client_id=session.client_id,
                client_secret=session.client_secret,
                pluggy_source=entry.get('pluggy_account_id') or entry.get('pluggy_item_id'),
                start_import_date=start_import_date,
                mappings=session.mappings,
                debug=debug,
                investment_filter=entry.get('investment_filter'),
            )
        except Exception as e:
            ui.error(f"Failed to set up '{label}': {e}")
            skipped.append(label)
            continue

        tasks.append((entry, importer, ynab_account))

    return tasks, skipped


def run_concurrently(tasks, work, present, *, total_label):
    """Run ``work(importer)`` for each task in parallel behind a live per-account
    progress display, returning ``{importer: result}`` for every task that
    returned (work that raises is shown as ✗ and omitted). ``present(result)``
    returns the ``(icon, detail)`` shown when a task finishes."""
    results = {}
    if not tasks:
        return results

    total = len(tasks)
    console.print(f"\n[head]{total_label} {total} account(s) from Pluggy[/]")
    with ui.make_fetch_progress() as progress:
        task_ids = {}
        for entry, importer, _ in tasks:
            task_ids[importer] = progress.add_task(
                entry.get('ynab_account', '?'), total=1, icon="", detail="[muted]fetching[/]"
            )
        with ThreadPoolExecutor(max_workers=min(total, MAX_WORKERS)) as pool:
            futures = {pool.submit(work, importer): importer for _, importer, _ in tasks}
            for future in as_completed(futures):
                importer = futures[future]
                tid = task_ids[importer]
                try:
                    result = future.result()
                    results[importer] = result
                    icon, detail = present(result)
                    progress.update(tid, completed=1, icon=icon, detail=detail)
                except Exception as e:
                    progress.update(tid, completed=1, icon="[err]✗[/]", detail=ui.fetch_error_detail(str(e)))
    return results


def _present_fetch(transactions):
    return "[ok]✓[/]", ui.fetch_detail(len(transactions))


def _present_diagnose(info):
    if info.get('ok'):
        return "[ok]✓[/]", f"[ok]{info.get('status') or 'reachable'}[/]"
    return "[err]✗[/]", ui.fetch_error_detail(info.get('error') or info.get('status') or 'unreachable')


def _present_balance(balance):
    if balance is None:
        return "[ok]✓[/]", "[muted]no balance[/]"
    return "[ok]✓[/]", f"[muted]{balance:,.2f}[/]"


# --------------------------------------------------------------------------- #
# Actions: sync / doctor / discover
# --------------------------------------------------------------------------- #

def run_sync(session, *, start_import_date, today, dry_run, update_investments,
             selected_labels, show_transactions, debug):
    session.connect_ynab()
    ynab_importer = YNABTransactionImporter(session.ynab, session.budget.id, start_import_date)
    tasks, skipped = build_importers(session, selected_labels, start_import_date, debug, require_ynab=True)

    results = run_concurrently(tasks, lambda imp: imp.get_data(), _present_fetch, total_label="Fetching")

    # Process on the main thread, in config order, so the shared transaction
    # list and YNAB writes stay race-free.
    reconciliations = []
    rendimentos = []
    for entry, importer, ynab_account in tasks:
        if importer not in results:  # fetch failed and was already shown
            skipped.append(entry.get('ynab_account', '?'))
            continue

        ynab_importer.add_transactions(results[importer])
        reconciliations.append({
            'name': entry['ynab_account'],
            'type': entry['type'],
            'pluggy_balance': importer.pluggy_balance,
            'positions': getattr(importer, 'matched_count', None),
        })

        # Optionally true up investment tracking accounts to the live balance.
        if update_investments and entry['type'] == 'investment':
            _maybe_post_rendimento(ynab_importer, entry, importer, ynab_account, today, rendimentos)

    if show_transactions:
        for entry, importer, _ in tasks:
            if importer in results:
                ui.render_transactions(entry['ynab_account'], results[importer])

    _save_and_render_summary(ynab_importer, dry_run=dry_run, skipped=skipped, rendimentos=rendimentos)
    _render_reconciliation(session, reconciliations, dry_run=dry_run)


def run_update_investments(session, today, debug):
    """Investment-only true-up: fetch each investment account's Pluggy balance and
    post the YNAB↔Pluggy difference as a 'Rendimento' adjustment. Non-investment
    accounts aren't fetched or shown. (The ``--update-investments`` flag instead
    layers this onto a full sync of all accounts, for the cron path.)"""
    session.connect_ynab()
    labels = session.investment_labels()
    if not labels:
        console.print("[warn]No investment accounts configured.[/]")
        return

    tasks, skipped = build_importers(session, labels, default_start_date(), debug, require_ynab=True)
    results = run_concurrently(tasks, lambda imp: imp.get_balance(), _present_balance,
                               total_label="Fetching balances for")

    ynab_importer = YNABTransactionImporter(session.ynab, session.budget.id, default_start_date())
    reconciliations = []
    rendimentos = []
    for entry, importer, ynab_account in tasks:
        if importer not in results:  # fetch failed and was already shown
            skipped.append(entry.get('ynab_account', '?'))
            continue
        reconciliations.append({
            'name': entry['ynab_account'],
            'type': entry['type'],
            'pluggy_balance': importer.pluggy_balance,
            'positions': getattr(importer, 'matched_count', None),
        })
        _maybe_post_rendimento(ynab_importer, entry, importer, ynab_account, today, rendimentos)

    _save_and_render_summary(ynab_importer, dry_run=False, skipped=skipped, rendimentos=rendimentos)
    _render_reconciliation(session, reconciliations, dry_run=False)


def _rendimento_import_id(today, account_id):
    # Unique per run (timestamp): YNAB permanently remembers import_ids and rejects
    # re-imports of a previously-used one as a duplicate (even after deletion), so a
    # fixed daily id would never recreate a deleted Rendimento. The diff check in
    # _maybe_post_rendimento is what keeps it idempotent — once trued up, the next
    # run sees diff≈0 and posts nothing.
    return f"REND-{today}-{datetime.now().strftime('%H%M%S')}-{account_id[:8]}"


def _maybe_post_rendimento(ynab_importer, entry, importer, ynab_account, today, rendimentos):
    """Queue a 'Rendimento' adjustment if this investment account's Pluggy balance
    drifts from YNAB beyond tolerance; records it in ``rendimentos`` for the summary."""
    if importer.pluggy_balance is None:
        return
    diff = round(importer.pluggy_balance * 1000) - ynab_account.balance
    if abs(diff) <= ui.RECONCILE_TOLERANCE:
        return
    ynab_importer.add_adjustment(
        account_id=ynab_account.id,
        amount=diff,
        date=today,
        payee="Rendimento",
        memo="Rendimento",
        import_id=_rendimento_import_id(today, ynab_account.id),
    )
    rendimentos.append({'name': entry['ynab_account'], 'amount': diff})


def _save_and_render_summary(ynab_importer, *, dry_run, skipped, rendimentos):
    queued = len(ynab_importer.transactions)
    imported = duplicates = api_error = None
    if not dry_run and queued:
        response = ynab_importer.save()
        if 'error' in response:
            err = response['error']
            api_error = f"{err.get('name')} - {err.get('detail')}"
        else:
            imported = len(response['data']['transaction_ids'])
            duplicates = len(response['data']['duplicate_import_ids'])

    ui.render_import_summary(dry_run=dry_run, queued=queued, imported=imported,
                             duplicates=duplicates, skipped=skipped,
                             rendimentos=rendimentos, api_error=api_error)


def _render_reconciliation(session, reconciliations, *, dry_run):
    if not reconciliations:
        return
    fresh = session.refresh_ynab_accounts()
    if not dry_run:
        # Keep the cached balances current so a later update in the same menu
        # session sees this run's adjustments and doesn't post them twice.
        session.ynab_accounts = fresh
    ui.render_reconciliation(reconciliations, {a.name: a for a in fresh})


def run_reconcile(session, selected_labels, debug):
    """Show only the balance reconciliation table: fetch each account's Pluggy
    balance (balances only, no transactions, no save) and compare to YNAB."""
    session.connect_ynab()
    tasks, skipped = build_importers(session, selected_labels, default_start_date(), debug, require_ynab=True)

    results = run_concurrently(tasks, lambda imp: imp.get_balance(), _present_balance,
                               total_label="Fetching balances for")

    reconciliations = []
    for entry, importer, _ in tasks:
        if importer not in results:  # fetch failed and was already shown
            skipped.append(entry.get('ynab_account', '?'))
            continue
        reconciliations.append({
            'name': entry['ynab_account'],
            'type': entry['type'],
            'pluggy_balance': importer.pluggy_balance,
            'positions': getattr(importer, 'matched_count', None),
        })

    if skipped:
        console.print(f"\n[err]! Accounts skipped/failed:[/] {', '.join(skipped)}")
    if reconciliations:
        ui.render_reconciliation(reconciliations, {a.name: a for a in session.ynab_accounts})


def run_doctor(session, selected_labels, debug):
    session.connect_ynab()
    tasks, _ = build_importers(session, selected_labels, default_start_date(), debug, require_ynab=False)

    results = run_concurrently(tasks, lambda imp: imp.diagnose(), _present_diagnose, total_label="Checking")

    rows = []
    for entry, importer, ynab_account in tasks:
        info = results.get(importer, {'ok': False, 'error': 'no result'})
        rows.append({
            'name': entry.get('ynab_account', '?'),
            'type': entry.get('type', '?'),
            'ynab_found': ynab_account is not None,
            **info,
        })
    ui.render_doctor(rows)


def run_discovery(session, item_id):
    """Discover the Pluggy account ids under an item, to fill in accounts.json."""
    api_key = requests.post(f"{PLUGGY_API}/auth", data={
        "clientId": session.client_id,
        "clientSecret": session.client_secret,
    }).json().get('apiKey')
    if not api_key:
        ui.auth_failed()
        return

    headers = {"X-API-KEY": api_key}
    response = requests.get(f"{PLUGGY_API}/accounts", params={"itemId": item_id}, headers=headers)
    if not response.ok:
        detail = response.json().get('message') if 'json' in response.headers.get('content-type', '') else response.text[:120]
        ui.discovery_error(response.status_code, detail)
        return

    accounts = response.json().get('results', [])
    investments = requests.get(f"{PLUGGY_API}/investments", params={"itemId": item_id}, headers=headers)
    inv_results = investments.json().get('results', []) if investments.ok else []
    ui.render_discovery(item_id, accounts, inv_results)


# --------------------------------------------------------------------------- #
# Interactive menu
# --------------------------------------------------------------------------- #

def run_menu(session, *, start_import_date, today, debug):
    if not session.has_accounts():
        return
    ui.banner(start_import_date)
    while True:
        action = ui.main_menu()
        if action == 'quit':
            break

        if action == 'sync':
            labels = ui.select_accounts(session.account_labels())
            if not labels:
                console.print("[warn]No accounts selected.[/]")
            else:
                show_transactions = ui.confirm_show_transactions()
                run_sync(session,
                         start_import_date=start_import_date, today=today,
                         dry_run=False, update_investments=False,
                         selected_labels=labels, show_transactions=show_transactions, debug=debug)
        elif action == 'update_investments':
            run_update_investments(session, today, debug)
        elif action == 'reconcile':
            run_reconcile(session, selected_labels=None, debug=debug)
        elif action == 'doctor':
            run_doctor(session, selected_labels=None, debug=debug)

        console.print()


# --------------------------------------------------------------------------- #
# Entry point
# --------------------------------------------------------------------------- #

def parse_args():
    parser = argparse.ArgumentParser(description='Sync transactions from Pluggy to YNAB')
    parser.add_argument('--from', dest='start_date', help='Start date for import (YYYY-MM-DD). Defaults to 14 days ago.')
    parser.add_argument('-n', '--dry-run', action='store_true',
                        help="Fetch and summarize everything, but don't save to YNAB.")
    parser.add_argument('-v', '--show-transactions', '--verbose', dest='show_transactions', action='store_true',
                        help='Also print the per-account transaction dump (hidden by default).')
    parser.add_argument('--debug', action='store_true',
                        help='Verbose output: per-page fetch counts and Pluggy totals.')
    parser.add_argument('--doctor', action='store_true',
                        help='Check each connection (auth + Pluggy reachability + freshness) and exit.')
    parser.add_argument('--reconcile', action='store_true',
                        help='Show only the balance reconciliation (Pluggy vs YNAB balances, no import) and exit.')
    parser.add_argument('--list-accounts', metavar='ITEM_ID',
                        help='List the Pluggy accounts/investments under an item id (to fill accounts.json) and exit.')
    parser.add_argument('--update-investments', action='store_true',
                        help="For investment accounts, post the YNAB↔Pluggy difference as a 'Rendimento' transaction so the tracking account catches up to the current balance.")
    parser.add_argument('--menu', action='store_true', help='Force the interactive menu.')
    parser.add_argument('--no-menu', action='store_true', help='Skip the interactive menu and run a sync with the given flags.')
    return parser.parse_args()


def main():
    load_dotenv()
    args = parse_args()
    session = Session()

    start_import_date = args.start_date or default_start_date()
    today = datetime.now().strftime('%Y-%m-%d')

    # One-shot, non-menu actions.
    if args.list_accounts:
        run_discovery(session, args.list_accounts)
        return
    if args.doctor:
        if session.has_accounts():
            run_doctor(session, selected_labels=None, debug=args.debug)
        return
    if args.reconcile:
        if session.has_accounts():
            run_reconcile(session, selected_labels=None, debug=args.debug)
        return

    # Show the menu when launched bare in a terminal; flags (and no TTY, e.g.
    # cron) run a sync non-interactively.
    any_action_flag = bool(args.start_date or args.dry_run or args.update_investments
                           or args.show_transactions or args.debug)
    interactive = args.menu or (sys.stdin.isatty() and not any_action_flag and not args.no_menu)

    if interactive:
        run_menu(session, start_import_date=start_import_date, today=today, debug=args.debug)
        return

    if not session.has_accounts():
        return
    ui.banner(start_import_date)
    run_sync(session,
             start_import_date=start_import_date, today=today,
             dry_run=args.dry_run, update_investments=args.update_investments,
             selected_labels=None, show_transactions=args.show_transactions, debug=args.debug)


if __name__ == '__main__':
    main()
