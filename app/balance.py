"""Balance computation and debt simplification (the core Splitwise math)."""

from collections import defaultdict

from .models import Expense, ExpenseParticipant, ExpensePayer, Payment


def expense_net(expense: Expense) -> dict[int, int]:
    """Net contribution of a single expense: +paid for payers, -share for participants."""
    net: dict[int, int] = defaultdict(int)
    for payer in expense.payers:
        net[payer.user_id] += payer.amount_cents
    for participant in expense.participants:
        net[participant.user_id] -= participant.share_cents
    return dict(net)


def payment_net(payment: Payment) -> dict[int, int]:
    """A payment from A to B settles A's debt to B: +for A (owed less), -for B (owed less)."""
    return {payment.from_user_id: payment.amount_cents, payment.to_user_id: -payment.amount_cents}


def combine_nets(nets: list[dict[int, int]]) -> dict[int, int]:
    out: dict[int, int] = defaultdict(int)
    for net in nets:
        for user_id, amount in net.items():
            out[user_id] += amount
    return dict(out)


def balance_for_expenses(
    expenses: list[Expense], payments: list[Payment]
) -> dict[int, int]:
    nets = [expense_net(e) for e in expenses] + [payment_net(p) for p in payments]
    return combine_nets(nets)


def simplify_debts(net: dict[int, int]) -> list[dict]:
    """Reduce net balances to a minimal list of transfers.

    Greedy algorithm: repeatedly transfer from the largest debtor to the
    largest creditor. Produces at most (n-1) transfers.
    Returns list of {"from_user_id", "to_user_id", "amount_cents"}.
    """
    debtors = [(uid, -amt) for uid, amt in net.items() if amt < 0]
    creditors = [(uid, amt) for uid, amt in net.items() if amt > 0]
    debtors.sort(key=lambda x: (-x[1], x[0]))
    creditors.sort(key=lambda x: (-x[1], x[0]))

    transfers: list[dict] = []
    i = j = 0
    while i < len(debtors) and j < len(creditors):
        debtor_id, debt = debtors[i]
        creditor_id, credit = creditors[j]
        amount = min(debt, credit)
        if amount > 0:
            transfers.append(
                {"from_user_id": debtor_id, "to_user_id": creditor_id, "amount_cents": amount}
            )
        if debt == amount:
            i += 1
        else:
            debtors[i] = (debtor_id, debt - amount)
        if credit == amount:
            j += 1
        else:
            creditors[j] = (creditor_id, credit - amount)
    return transfers


def pair_balance(user_a: int, user_b: int, net: dict[int, int]) -> int:
    """Net amount user A is owed by B (positive = B owes A, negative = A owes B)."""
    return net.get(user_a, 0)
