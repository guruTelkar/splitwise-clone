"""Shared service helpers: serialization, membership, activity, currency."""

from sqlalchemy.orm import Session

from .models import ActivityEvent, Expense, Group, GroupMember, Notification, User

# Approximate conversion rates relative to 1 USD (used by the Pro currency feature).
# Updated statically for a self-hosted clone; refresh via the rates endpoint.
USD_RATES: dict[str, float] = {
    "USD": 1.0,
    "EUR": 0.92,
    "GBP": 0.79,
    "INR": 83.2,
    "JPY": 156.4,
    "AUD": 1.52,
    "CAD": 1.37,
    "CHF": 0.90,
    "CNY": 7.24,
    "SGD": 1.35,
    "AED": 3.67,
    "BRL": 5.45,
    "NZD": 1.64,
    "SEK": 10.55,
    "KRW": 1365.0,
    "MXN": 17.9,
    "RUB": 92.0,
    "HKD": 7.81,
    "ZAR": 18.7,
    "TRY": 32.4,
}


def convert_cents(amount_cents: int, from_currency: str, to_currency: str) -> int:
    if from_currency == to_currency:
        return amount_cents
    rate_from = USD_RATES.get(from_currency, 1.0)
    rate_to = USD_RATES.get(to_currency, 1.0)
    return round(amount_cents / rate_from * rate_to)


def group_member_ids(db: Session, group_id: int) -> list[int]:
    rows = db.query(GroupMember.user_id).filter(GroupMember.group_id == group_id).all()
    return [r[0] for r in rows]


def is_group_member(db: Session, group_id: int, user_id: int) -> bool:
    return (
        db.query(GroupMember)
        .filter(GroupMember.group_id == group_id, GroupMember.user_id == user_id)
        .first()
        is not None
    )


def get_group_or_404(db: Session, group_id: int) -> Group:
    group = db.get(Group, group_id)
    if group is None:
        from fastapi import HTTPException
        raise HTTPException(status_code=404, detail="Group not found")
    return group


def serialize_user(user: User) -> dict:
    return {
        "id": user.id,
        "email": user.email,
        "name": user.name,
        "base_currency": user.base_currency,
        "avatar_url": user.avatar_url,
        "mobile": user.mobile,
        "is_pro": user.is_pro,
    }


def serialize_participant(p) -> dict:
    return {
        "user_id": p.user_id,
        "share_cents": p.share_cents,
        "weight": p.weight,
    }


def serialize_expense(expense: Expense) -> dict:
    return {
        "id": expense.id,
        "group_id": expense.group_id,
        "description": expense.description,
        "amount_cents": expense.amount_cents,
        "currency": expense.currency,
        "date": expense.expense_date,
        "category": expense.category,
        "notes": expense.notes,
        "receipt_url": expense.receipt_url,
        "location": expense.location,
        "created_by": expense.created_by,
        "is_recurring": expense.is_recurring,
        "created_at": expense.created_at.isoformat(),
        "payers": [
            {"user_id": p.user_id, "amount_cents": p.amount_cents} for p in expense.payers
        ],
        "participants": [serialize_participant(p) for p in expense.participants],
    }


def create_activity(
    db: Session,
    group_id: int | None,
    actor_id: int,
    event_type: str,
    payload: dict,
) -> ActivityEvent:
    event = ActivityEvent(
        group_id=group_id, actor_id=actor_id, event_type=event_type, payload=payload
    )
    db.add(event)
    return event


def notify(db: Session, user_id: int, message: str, link: str | None = None) -> None:
    db.add(Notification(user_id=user_id, message=message, link=link))
