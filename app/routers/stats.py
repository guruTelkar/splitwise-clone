from datetime import datetime

from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy.orm import Session

from ..balance import balance_for_expenses, simplify_debts
from ..database import get_db
from ..deps import get_current_user
from ..models import Expense, Group, GroupMember, Payment, User
from ..service import convert_cents, get_group_or_404, is_group_member

router = APIRouter(prefix="/stats", tags=["stats"])


def _members(db: Session, group_id: int) -> list[User]:
    rows = db.query(GroupMember.user_id).filter(GroupMember.group_id == group_id).all()
    ids = [r[0] for r in rows]
    return list(db.query(User).filter(User.id.in_(ids)))


@router.get("/group/{group_id}")
def group_stats(
    group_id: int,
    user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    if not is_group_member(db, group_id, user.id):
        raise HTTPException(status_code=403, detail="Not a member of this group")

    group = get_group_or_404(db, group_id)
    base = group.currency

    expenses = db.query(Expense).filter(Expense.group_id == group_id).all()
    payments = db.query(Payment).filter(Payment.group_id == group_id).all()

    total_spent = sum(convert_cents(e.amount_cents, e.currency, base) for e in expenses)

    by_category: dict[str, int] = {}
    monthly_spent: dict[str, int] = {}
    for e in expenses:
        converted = convert_cents(e.amount_cents, e.currency, base)
        by_category[e.category] = by_category.get(e.category, 0) + converted
        month = e.expense_date[:7]
        monthly_spent[month] = monthly_spent.get(month, 0) + converted

    per_member_paid: dict[int, int] = {}
    per_member_share: dict[int, int] = {}
    for e in expenses:
        for p in e.payers:
            per_member_paid[p.user_id] = per_member_paid.get(p.user_id, 0) + convert_cents(
                p.amount_cents, e.currency, base
            )
        for p in e.participants:
            per_member_share[p.user_id] = per_member_share.get(p.user_id, 0) + convert_cents(
                p.share_cents, e.currency, base
            )

    members = _members(db, group_id)
    member_rows = []
    for m in members:
        paid = per_member_paid.get(m.id, 0)
        share = per_member_share.get(m.id, 0)
        member_rows.append(
            {
                "user": {"id": m.id, "name": m.name},
                "paid_cents": paid,
                "share_cents": share,
                "net_cents": paid - share,
            }
        )

    # Balances over time (monthly running net for each member)
    net = balance_for_expenses(expenses, payments)
    months: dict[str, dict[int, int]] = {}
    for e in sorted(expenses, key=lambda x: x.expense_date):
        month = e.expense_date[:7]
        bucket = months.setdefault(month, {})
        for p in e.payers:
            delta = convert_cents(p.amount_cents, e.currency, base)
            bucket[p.user_id] = bucket.get(p.user_id, 0) + delta
        for p in e.participants:
            delta = convert_cents(p.share_cents, e.currency, base)
            bucket[p.user_id] = bucket.get(p.user_id, 0) - delta
    for p in payments:
        month = p.created_at.strftime("%Y-%m")
        bucket = months.setdefault(month, {})
        delta = convert_cents(p.amount_cents, p.currency, base)
        bucket[p.from_user_id] = bucket.get(p.from_user_id, 0) + delta
        bucket[p.to_user_id] = bucket.get(p.to_user_id, 0) - delta
    running: dict[int, int] = {}
    balance_over_time = []
    for month in sorted(months):
        for uid, delta in months[month].items():
            running[uid] = running.get(uid, 0) + delta
        balance_over_time.append(
            {
                "month": month,
                "balances": [{"user_id": uid, "balance_cents": amt} for uid, amt in running.items()],
            }
        )

    return {
        "group_id": group_id,
        "currency": base,
        "total_spent_cents": total_spent,
        "expense_count": len(expenses),
        "monthly_spent": [
            {"month": m, "amount_cents": v}
            for m, v in sorted(monthly_spent.items())
        ],
        "by_category": [
            {"category": k, "amount_cents": v} for k, v in sorted(by_category.items(), key=lambda x: -x[1])
        ],
        "members": member_rows,
        "simplified_debts": simplify_debts(net),
        "balance_over_time": balance_over_time,
    }
