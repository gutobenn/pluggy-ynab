"""Presentation and interaction layer (rich + questionary).

Centralizes every bit of console output and every interactive prompt so that
``sync.py`` stays orchestration-only. This module deliberately does **not**
import anything from the ``importers`` package: ``importers/base.py`` imports the
shared :data:`console` from here, and keeping the dependency one-directional
avoids an import cycle. ``sync.py`` passes plain data into the ``render_*``
functions below.
"""
from typing import List, Optional

import questionary
from rich.console import Console, Group
from rich.markup import escape
from rich.panel import Panel
from rich.progress import Progress, SpinnerColumn, TextColumn
from rich.table import Table
from rich.text import Text
from rich.theme import Theme

THEME = Theme({
    "ok": "green",
    "warn": "yellow",
    "err": "bold red",
    "head": "bold cyan",
    "muted": "dim",
    "credit": "green",
    "debit": "red",
})

# The single shared console. Using one instance everywhere means debug/log lines
# emitted during the concurrent fetch print cleanly *above* an active Progress.
# highlight=False: every color here is deliberate, so we suppress rich's automatic
# number/quote/paren highlighting.
console = Console(theme=THEME, highlight=False)

# YNAB and Pluggy balances are compared to the cent; allow sub-cent rounding.
RECONCILE_TOLERANCE = 10  # milliunits (R$0,01)

RECONCILE_SECTIONS = [
    ('checking', 'Checking'),
    ('credit_card', 'Credit cards'),
    ('investment', 'Investments'),
]

# Pluggy item statuses we consider healthy (anything else still reachable but stale).
HEALTHY_STATUSES = ('UPDATED', 'UPDATING', None)


def money(milliunits) -> str:
    """Format a milliunit integer as a plain 2-decimal string."""
    return f"{milliunits / 1000:,.2f}"


# --------------------------------------------------------------------------- #
# Banner + live fetch progress
# --------------------------------------------------------------------------- #

def banner(start_date: str):
    console.print(f"[head]pluggy-ynab[/] — importing transactions since [bold]{start_date}[/]")


def make_fetch_progress() -> Progress:
    """Live per-account display: a spinner per account while it fetches, then a
    ✓/✗ icon and a short detail (txn count or error). Tasks are rendered as an
    aligned grid, so columns line up across accounts automatically."""
    return Progress(
        SpinnerColumn(finished_text=" "),
        TextColumn("{task.fields[icon]}"),
        TextColumn("{task.description}"),
        TextColumn("{task.fields[detail]}"),
        console=console,
    )


def fetch_detail(count: int, noun: str = "txns") -> str:
    return f"[muted]{count} {noun}[/]"


def fetch_error_detail(error: str) -> str:
    return f"[err]{escape(error)}[/]"


# --------------------------------------------------------------------------- #
# Interactive menu (questionary)
# --------------------------------------------------------------------------- #

# Highlight the option under the cursor (and the pointer/marks) in cyan so the
# current selection stands out.
MENU_STYLE = questionary.Style([
    ("qmark", "fg:cyan bold"),
    ("question", "bold"),
    ("pointer", "fg:cyan bold"),
    ("highlighted", "fg:cyan bold"),
    ("selected", "fg:cyan bold"),
    ("answer", "fg:cyan bold"),
    ("instruction", "fg:#808080"),
])


def main_menu() -> str:
    """Return the chosen action key, or 'quit' on cancel (Ctrl-C / Esc)."""
    choice = questionary.select(
        "What would you like to do?",
        choices=[
            questionary.Choice("Sync transactions", value="sync"),
            questionary.Choice("Update investments (Rendimento)", value="update_investments"),
            questionary.Choice("Balance reconciliation", value="reconcile"),
            questionary.Choice("Reconcile & lock (mark cleared as reconciled)", value="reconcile_commit"),
            questionary.Choice("Check connections", value="doctor"),
            questionary.Choice("Quit", value="quit"),
        ],
        style=MENU_STYLE,
    ).ask()
    return choice or "quit"


def select_accounts(labels: List[str]) -> List[str]:
    """Checklist of account labels, all pre-checked. Enter accepts all."""
    if not labels:
        return []
    choices = [questionary.Choice(label, value=label, checked=True) for label in labels]
    result = questionary.checkbox(
        "Select accounts (space toggles, enter confirms):", choices=choices, style=MENU_STYLE
    ).ask()
    return result if result is not None else []


def confirm_show_transactions() -> bool:
    return bool(questionary.confirm(
        "Show per-account transaction details?", default=False, style=MENU_STYLE
    ).ask())


def confirm_reconcile_lock(account_count: int, txn_count: int) -> bool:
    return bool(questionary.confirm(
        f"Lock {account_count} matched account(s) — {txn_count} cleared transaction(s) — "
        "as reconciled? This can't be undone from this CLI.",
        default=False, style=MENU_STYLE,
    ).ask())


# --------------------------------------------------------------------------- #
# Transaction dump (opt-in)
# --------------------------------------------------------------------------- #

def render_transactions(name: str, transactions: list):
    """Per-account transaction dump (newest first). Opt-in only."""
    table = Table(header_style="muted", box=None, pad_edge=False)
    table.add_column("Date", no_wrap=True)
    table.add_column("Amount", justify="right")
    table.add_column("Type", no_wrap=True)
    table.add_column("Payee")
    table.add_column("Memo", style="muted")

    for transaction in reversed(transactions):
        amount = transaction['amount'] / 1000
        is_credit = amount >= 0
        amount_text = Text(f"{amount:,.2f}", style="credit" if is_credit else "debit")
        type_text = Text("CREDIT", style="credit") if is_credit else Text("DEBIT", style="debit")
        payee = transaction['payee']
        memo = transaction.get('memo', '')
        memo = memo if memo and memo != payee else ""
        table.add_row(transaction['date'], amount_text, type_text, Text(payee), Text(memo))

    console.print(f"\n[warn]{escape(name)} ({len(transactions)})[/]")
    console.print(table)


# --------------------------------------------------------------------------- #
# Import summary panel
# --------------------------------------------------------------------------- #

def render_import_summary(*, dry_run: bool, queued: int, imported: Optional[int],
                          duplicates: Optional[int], skipped: List[str],
                          rendimentos: List[dict], api_error: Optional[str],
                          transfers: Optional[List[dict]] = None):
    lines = []
    if dry_run:
        lines.append("[warn]DRY RUN — nothing saved to YNAB.[/]")
        lines.append(f"Transactions that would be imported: [bold]{queued}[/]")
    elif api_error:
        lines.append(f"[err]YNAB API error:[/] {escape(api_error)}")
    elif queued == 0:
        lines.append("[warn]Nothing to import.[/]")
    else:
        lines.append(f"[ok]+ New transactions imported:[/] [bold]{imported}[/]")
        lines.append(f"[warn]= Duplicate transactions:[/] [bold]{duplicates}[/]")

    if transfers:
        lines.append(f"[ok]⇄ Transfers deduplicated:[/] [bold]{len(transfers)}[/]")
        for transfer in transfers:
            lines.append(
                f"  {escape(transfer['from'])} → {escape(transfer['to'])}: "
                f"{money(abs(transfer['amount']))} ({transfer['date']})"
            )

    for rendimento in rendimentos:
        lines.append(f"[ok]Rendimento[/] {escape(rendimento['name'])}: {money(rendimento['amount'])}")

    if skipped:
        lines.append(f"[err]! Accounts skipped/failed:[/] {escape(', '.join(skipped))}")

    body = Group(*[Text.from_markup(line) for line in lines])
    console.print()
    console.print(Panel(body, title="Import Summary", title_align="left",
                        border_style="head", expand=False, padding=(0, 2)))


# --------------------------------------------------------------------------- #
# Balance reconciliation
# --------------------------------------------------------------------------- #

def _recon_table() -> Table:
    table = Table(header_style="muted", expand=False, padding=(0, 1))
    table.add_column("Account")
    table.add_column("YNAB clr", justify="right")
    table.add_column("YNAB unclr", justify="right")
    table.add_column("YNAB total", justify="right")
    table.add_column("Pluggy", justify="right")
    table.add_column("Diff", justify="right")
    table.add_column("Status")
    return table


def _add_recon_row(table: Table, row: dict, ynab_by_name: dict):
    account = ynab_by_name.get(row['name'])
    name = Text(row['name'])
    if account is None:
        table.add_row(name, "", "", "", "", "", Text("YNAB account not found", style="err"))
        return

    cleared = account.cleared_balance
    uncleared = account.uncleared_balance
    total = cleared + uncleared
    pluggy = row['pluggy_balance']

    if pluggy is None:
        table.add_row(name, money(cleared), money(uncleared), money(total),
                      "?", "?", Text("no Pluggy balance", style="warn"))
        return

    pluggy_milli = round(pluggy * 1000)
    # Credit cards: Pluggy reports the amount owed (positive); YNAB shows it negative.
    expected = -pluggy_milli if row['type'] == 'credit_card' else pluggy_milli
    diff = total - expected
    matched = abs(diff) <= RECONCILE_TOLERANCE
    status = Text("match", style="ok") if matched else Text("MISMATCH", style="err")
    if row['type'] == 'investment' and row.get('positions') is not None:
        status.append(f"  ({row['positions']} pos.)", style="muted")

    table.add_row(name, money(cleared), money(uncleared), money(total),
                  money(pluggy_milli), money(diff), status)


def render_reconciliation(rows: list, ynab_by_name: dict):
    """Compare each account's Pluggy balance against YNAB (cleared + uncleared),
    grouped by type, flagging match/mismatch."""
    console.print()
    console.print("[head]Balance Reconciliation[/]")

    by_type = {}
    for row in rows:
        by_type.setdefault(row['type'], []).append(row)
    ordered = RECONCILE_SECTIONS + [(t, t.upper()) for t in by_type if t not in dict(RECONCILE_SECTIONS)]

    for type_key, title in ordered:
        group = by_type.get(type_key)
        if not group:
            continue
        table = _recon_table()
        for row in group:
            _add_recon_row(table, row, ynab_by_name)
        console.print(f"\n[head]{title}[/]")
        console.print(table)

    console.print(
        "[muted]Note: compares Pluggy's current balance to YNAB (cleared + uncleared). "
        "Credit cards are sign-inverted. Investments are report-only unless you pass "
        "--update-investments, which posts the difference as a 'Rendimento' transaction. "
        "A mismatch can also mean missing/extra transactions or history older than --from "
        "not in YNAB.[/]"
    )


# --------------------------------------------------------------------------- #
# Reconcile & lock summary
# --------------------------------------------------------------------------- #

def render_reconcile_lock_summary(*, locked: List[dict], skipped: List[dict]):
    """``locked``: [{'name', 'count'}] of accounts whose cleared transactions were
    marked reconciled. ``skipped``: [{'name', 'detail', 'kind'}] where ``kind`` is
    'error' (red) or anything else (yellow — mismatch / nothing to lock / investment)."""
    lines = []
    if locked:
        total = sum(item['count'] for item in locked)
        lines.append(f"[ok]✓ Reconciled (locked):[/] [bold]{total}[/] transaction(s) "
                     f"across {len(locked)} account(s)")
        for item in locked:
            lines.append(f"  {escape(item['name'])}: [bold]{item['count']}[/]")
    else:
        lines.append("[warn]Nothing locked.[/]")

    for item in skipped:
        style = "err" if item.get('kind') == 'error' else "warn"
        lines.append(f"[{style}]• {escape(item['name'])}[/] — {escape(item['detail'])}")

    body = Group(*[Text.from_markup(line) for line in lines])
    console.print()
    console.print(Panel(body, title="Reconcile & Lock", title_align="left",
                        border_style="head", expand=False, padding=(0, 2)))


# --------------------------------------------------------------------------- #
# Doctor (connection health)
# --------------------------------------------------------------------------- #

def render_doctor(rows: list):
    console.print()
    console.print("[head]Connection Health[/]")
    table = Table(header_style="muted", expand=False, padding=(0, 1))
    table.add_column("Account")
    table.add_column("Type", no_wrap=True)
    table.add_column("Connection")
    table.add_column("Balance", justify="right")
    table.add_column("Updated", no_wrap=True)
    table.add_column("YNAB")

    for row in rows:
        if row.get('ok'):
            status = row.get('status') or 'reachable'
            connection = Text(f"✓ {status}", style="ok")
        else:
            detail = row.get('error') or row.get('status') or 'unreachable'
            connection = Text(f"✗ {detail}", style="err")

        balance = row.get('balance')
        if balance is None:
            balance_text = "?"
        elif row['type'] == 'investment':
            positions = row.get('positions')
            suffix = f"  ({positions} pos.)" if positions is not None else ""
            balance_text = f"{balance:,.2f}{suffix}"
        else:
            balance_text = f"{balance:,.2f}"

        updated = (row.get('last_updated') or "")[:16].replace('T', ' ')
        ynab = Text("✓", style="ok") if row.get('ynab_found') else Text("✗ not found", style="err")

        table.add_row(Text(row['name']), row['type'], connection, balance_text, updated, ynab)

    console.print(table)


# --------------------------------------------------------------------------- #
# Account discovery (--list-accounts)
# --------------------------------------------------------------------------- #

def render_discovery(item_id: str, accounts: list, investments: list):
    console.print()
    console.print(f"[head]Pluggy accounts for item {escape(item_id)}[/]")

    if not accounts:
        console.print("  [warn]No accounts found.[/] Verify the item id and that it belongs to this Pluggy app.")
    else:
        table = Table(header_style="muted", expand=False, padding=(0, 1))
        table.add_column("Pluggy account id", no_wrap=True)
        table.add_column("Type")
        table.add_column("Balance", justify="right")
        table.add_column("Name")
        table.add_column("accounts.json")
        for account in accounts:
            config_type = 'credit_card' if account.get('type') == 'CREDIT' else 'checking'
            balance = account.get('balance')
            balance_text = f"{balance:,.2f}" if balance is not None else "?"
            snippet = f'"type": "{config_type}", "pluggy_account_id": "{account["id"]}"'
            table.add_row(
                Text(account['id'], style="ok"),
                f"{account.get('type')}/{account.get('subtype')}",
                balance_text,
                Text(str(account.get('name'))),
                Text(snippet, style="muted"),
            )
        console.print(table)

    if investments:
        total = sum(i.get('balance') or 0 for i in investments)
        console.print(
            f"\n  [bold]Investments:[/] {len(investments)} found, total balance={total:,.2f}"
        )
        console.print(
            f"  [muted]-> for an investment account use the item id itself: "
            f'"type": "investment", "pluggy_item_id": "{escape(item_id)}"[/]'
        )


def discovery_error(status_code: int, detail: str):
    console.print(f"  [err]HTTP {status_code}:[/] {escape(str(detail))}")
    console.print("  [muted]Check the item id at dashboard.pluggy.ai (it must belong to this Pluggy application).[/]")


def auth_failed():
    console.print("[err]Pluggy authentication failed.[/] Check PLUGGY_CLIENT_ID / PLUGGY_CLIENT_SECRET in .env.")


def error(message: str):
    console.print(f"[err]{escape(message)}[/]")
