"""Detect transfers between two synced accounts and pair their two legs.

When the user moves money between their own accounts, both accounts' Pluggy
feeds report it — a debit on the sender, a credit on the receiver. Left alone,
that lands in YNAB as two unrelated transactions (one looks like an expense, the
other like income). This module pairs the two legs so the caller can post a
single YNAB transfer instead.

Matching runs on the already-mapped ``Transaction`` dicts, so it works on the
post-mapping YNAB-convention ``amount`` — which already accounts for the credit
card sign inversion. The same rule therefore covers checking↔checking and
checking↔credit-card (a card payment is a negative debit on checking and a
positive inflow on the card). A pair must be:

1. across two *different* YNAB accounts,
2. equal magnitude / opposite sign (exact integer milliunits — no tolerance),
3. dated within ``window_days`` of each other (settlement can lag a day or two),
4. mutually unique: each leg is the other's only viable candidate. Ambiguous
   matches (two candidates with the same amount and date) are left untouched —
   we never risk a wrong pairing.
"""
from collections import namedtuple
from datetime import datetime

DEDUP_WINDOW_DAYS = 3

# debit/credit are the mapped Transaction dicts (sender/receiver legs);
# debit_acct/credit_acct are the YNAB Account objects (with .id, .transfer_payee_id).
TransferPair = namedtuple('TransferPair', 'debit credit debit_acct credit_acct')


def _days_apart(date_a: str, date_b: str) -> int:
    a = datetime.strptime(date_a, '%Y-%m-%d')
    b = datetime.strptime(date_b, '%Y-%m-%d')
    return abs((a - b).days)


def _are_candidates(a, acct_a, b, acct_b, window_days: int) -> bool:
    return (
        acct_a.id != acct_b.id
        and a['amount'] != 0
        and a['amount'] == -b['amount']
        and _days_apart(a['date'], b['date']) <= window_days
    )


def _resolve(leg, candidate_indices, legs):
    """Pick this leg's single best counterpart index, or None if none/ambiguous.

    One candidate → it. Several → disambiguate by the counterparty document
    (a same-owner transfer carries the user's own CPF/CNPJ on both legs); if that
    still doesn't single one out, give up and leave the leg unpaired."""
    if not candidate_indices:
        return None
    if len(candidate_indices) == 1:
        return candidate_indices[0]
    document = leg.get('counterparty_document')
    if document:
        doc_matches = [j for j in candidate_indices
                       if legs[j][0].get('counterparty_document') == document]
        if len(doc_matches) == 1:
            return doc_matches[0]
    return None


def find_transfer_pairs(account_txns, window_days: int = DEDUP_WINDOW_DAYS):
    """Pair transfer legs across accounts.

    ``account_txns``: list of ``(ynab_account, [Transaction, ...])``.
    Returns ``(pairs, consumed_ids)`` — a list of :class:`TransferPair` and the
    set of ``transaction_id`` values that were paired (and so must be skipped
    from the normal per-account import)."""
    legs = [(txn, acct) for acct, txns in account_txns for txn in txns]

    candidates = {i: [] for i in range(len(legs))}
    for i, (a, acct_a) in enumerate(legs):
        for j, (b, acct_b) in enumerate(legs):
            if i != j and _are_candidates(a, acct_a, b, acct_b, window_days):
                candidates[i].append(j)

    pairs = []
    consumed = set()        # leg indices already paired
    consumed_ids = set()    # their transaction_ids

    for i, (a, acct_a) in enumerate(legs):
        if i in consumed:
            continue
        j = _resolve(a, [c for c in candidates[i] if c not in consumed], legs)
        if j is None:
            continue
        # Mutual uniqueness: i must also be j's resolved choice.
        back = _resolve(legs[j][0], [c for c in candidates[j] if c not in consumed], legs)
        if back != i:
            continue

        b, acct_b = legs[j]
        # The debit (negative / outgoing) leg is the sender.
        if a['amount'] < 0:
            pair = TransferPair(a, b, acct_a, acct_b)
        else:
            pair = TransferPair(b, a, acct_b, acct_a)
        pairs.append(pair)
        consumed.update((i, j))
        consumed_ids.update((a['transaction_id'], b['transaction_id']))

    return pairs, consumed_ids
