from datetime import datetime, timedelta

from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy.orm import Session

from ..database import get_db
from ..deps import get_current_user
from ..models import (
    Comment,
    Expense,
    ExpenseParticipant,
    ExpensePayer,
    Group,
    Recurring,
    Reminder,
    User,
)
from ..schemas import (
    CreateCommentRequest,
    CreateExpenseRequest,
    CreateRecurringRequest,
    CreateReminderRequest,
)
from ..service import (
    create_activity,
    get_group_or_404,
    group_member_ids,
    is_group_member,
    notify,
    serialize_expense,
)

router = APIRouter(prefix="/expenses", tags=["expenses"])
recurring_router = APIRouter(prefix="/recurring", tags=["recurring"])


def distribute(total_cents: int, weights: list[float]) -> list[int]:
    """Split total_cents into integer parts proportional to weights, summing exactly."""
    if not weights:
        return []
    weight_sum = sum(weights)
    if weight_sum <= 0:
        weights = [1.0] * len(weights)
        weight_sum = len(weights)
    raw = [total_cents * w / weight_sum for w in weights]
    floors = [int(r) for r in raw]
    diff = total_cents - sum(floors)
    order = sorted(
        range(len(raw)), key=lambda i: (raw[i] - floors[i], i), reverse=True
    )
    for k in range(diff):
        floors[order[k % len(order)]] += 1
    return floors


def _validate_expense_context(db: Session, user: User, payload: CreateExpenseRequest):
    if payload.group_id is not None:
        group = get_group_or_404(db, payload.group_id)
        if not is_group_member(db, group.id, user.id):
            raise HTTPException(status_code=403, detail="Not a member of this group")
        allowed_ids = set(group_member_ids(db, group.id))
    else:
        if len(payload.participants) != 2:
            raise HTTPException(
                status_code=400, detail="Non-group (IOU) expenses need exactly 2 participants"
            )
        allowed_ids = set(payload.participants) | {p.user_id for p in payload.payers}
    if user.id not in allowed_ids:
        raise HTTPException(status_code=403, detail="You must be part of this expense")
    for p in payload.payers:
        if p.user_id not in allowed_ids:
            raise HTTPException(
                status_code=400, detail=f"User {p.user_id} cannot pay in this expense"
            )
    for pid in payload.participants:
        if pid not in allowed_ids:
            raise HTTPException(
                status_code=400, detail=f"User {pid} is not part of this expense context"
            )


def _compute_shares(payload: CreateExpenseRequest, amount_cents: int) -> dict[int, int]:
    n = len(payload.participants)
    shares = payload.shares or []
    weights = payload.weights or []
    if payload.split_method in ("amounts", "adjustment"):
        if len(shares) != n:
            raise HTTPException(status_code=400, detail="Provide one amount per participant")
        out = {uid: round(s * 100) for uid, s in zip(payload.participants, shares)}
        total = sum(out.values())
        if abs(total - amount_cents) > 1:
            raise HTTPException(
                status_code=400, detail=f"Amounts must sum to the expense total (got {total})"
            )
        return out
    if payload.split_method in ("percentages", "shares"):
        if len(shares) != n:
            raise HTTPException(
                status_code=400,
                detail="Provide one value per participant for percentages/shares",
            )
        weights = shares
    if not weights:
        weights = [1.0] * n
    parts = distribute(amount_cents, weights)
    return {uid: parts[i] for i, uid in enumerate(payload.participants)}


def _build_expense(db: Session, user: User, payload: CreateExpenseRequest) -> Expense:
    amount_cents = round(payload.amount * 100)
    shares = _compute_shares(payload, amount_cents)

    expense = Expense(
        group_id=payload.group_id,
        description=payload.description.strip(),
        amount_cents=amount_cents,
        currency=payload.currency,
        expense_date=payload.date or datetime.now().strftime("%Y-%m-%d"),
        category=payload.category or "General",
        notes=payload.notes,
        receipt_url=payload.receipt_url,
        created_by=user.id,
    )
    for payer in payload.payers:
        expense.payers.append(
            ExpensePayer(
                user_id=payer.user_id, amount_cents=round(payer.amount * 100)
            )
        )
    weights = payload.weights or []
    for i, uid in enumerate(payload.participants):
        weight = weights[i] if i < len(weights) and weights[i] else 1.0
        expense.participants.append(
            ExpenseParticipant(user_id=uid, share_cents=shares[uid], weight=weight)
        )
    db.add(expense)
    return expense


@router.post("")
def create_expense(
    payload: CreateExpenseRequest,
    user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    _validate_expense_context(db, user, payload)
    expense = _build_expense(db, user, payload)
    db.commit()
    db.refresh(expense)

    participants = [db.get(User, uid) for uid in payload.participants]
    create_activity(
        db,
        expense.group_id,
        user.id,
        "expense_added",
        {
            "expense_id": expense.id,
            "description": expense.description,
            "amount_cents": expense.amount_cents,
            "currency": expense.currency,
        },
    )
    for participant in participants:
        if participant and participant.id != user.id:
            notify(
                db,
                participant.id,
                f"{user.name} added '{expense.description}' ({expense.amount_cents / 100:.2f} {expense.currency})",
                f"expense/{expense.id}",
            )
    db.commit()
    return serialize_expense(expense)


@router.get("/{expense_id}")
def get_expense(
    expense_id: int,
    user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    expense = db.get(Expense, expense_id)
    if expense is None:
        raise HTTPException(status_code=404, detail="Expense not found")
    _check_expense_access(db, user, expense)
    data = serialize_expense(expense)
    data["comments"] = [
        {
            "id": c.id,
            "user_id": c.user_id,
            "text": c.text,
            "created_at": c.created_at.isoformat(),
        }
        for c in expense.comments
    ]
    return data


def _check_expense_access(db: Session, user: User, expense: Expense):
    if expense.group_id is not None:
        if not is_group_member(db, expense.group_id, user.id):
            raise HTTPException(status_code=403, detail="No access to this expense")
    else:
        ids = {p.user_id for p in expense.participants}
        if user.id not in ids:
            raise HTTPException(status_code=403, detail="No access to this expense")


@router.put("/{expense_id}")
def update_expense(
    expense_id: int,
    payload: CreateExpenseRequest,
    user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    expense = db.get(Expense, expense_id)
    if expense is None:
        raise HTTPException(status_code=404, detail="Expense not found")
    _check_expense_access(db, user, expense)
    if expense.created_by != user.id:
        raise HTTPException(
            status_code=403, detail="Only the creator can edit an expense"
        )
    _validate_expense_context(db, user, payload)

    amount_cents = round(payload.amount * 100)
    shares = _compute_shares(payload, amount_cents)

    expense.description = payload.description.strip()
    expense.amount_cents = amount_cents
    expense.currency = payload.currency
    expense.expense_date = payload.date or datetime.now().strftime("%Y-%m-%d")
    expense.category = payload.category or "General"
    expense.notes = payload.notes
    expense.receipt_url = payload.receipt_url

    expense.payers.clear()
    expense.participants.clear()
    db.flush()
    for payer in payload.payers:
        expense.payers.append(
            ExpensePayer(user_id=payer.user_id, amount_cents=round(payer.amount * 100))
        )
    weights = payload.weights or []
    for i, uid in enumerate(payload.participants):
        weight = weights[i] if i < len(weights) and weights[i] else 1.0
        expense.participants.append(
            ExpenseParticipant(user_id=uid, share_cents=shares[uid], weight=weight)
        )
    db.commit()
    db.refresh(expense)
    create_activity(
        db,
        expense.group_id,
        user.id,
        "expense_updated",
        {"expense_id": expense.id, "description": expense.description},
    )
    db.commit()
    return serialize_expense(expense)


@router.delete("/{expense_id}")
def delete_expense(
    expense_id: int,
    user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    expense = db.get(Expense, expense_id)
    if expense is None:
        raise HTTPException(status_code=404, detail="Expense not found")
    _check_expense_access(db, user, expense)
    if expense.created_by != user.id:
        raise HTTPException(
            status_code=403, detail="Only the creator can delete an expense"
        )
    create_activity(
        db,
        expense.group_id,
        user.id,
        "expense_deleted",
        {"description": expense.description, "amount_cents": expense.amount_cents, "currency": expense.currency},
    )
    db.delete(expense)
    db.commit()
    return {"ok": True}


@router.post("/{expense_id}/comments")
def add_comment(
    expense_id: int,
    payload: CreateCommentRequest,
    user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    expense = db.get(Expense, expense_id)
    if expense is None:
        raise HTTPException(status_code=404, detail="Expense not found")
    _check_expense_access(db, user, expense)
    comment = Comment(expense_id=expense_id, user_id=user.id, text=payload.text.strip())
    db.add(comment)
    db.commit()
    db.refresh(comment)
    return {
        "id": comment.id,
        "user_id": comment.user_id,
        "text": comment.text,
        "created_at": comment.created_at.isoformat(),
    }


@router.get("/{expense_id}/comments")
def list_comments(
    expense_id: int,
    user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    expense = db.get(Expense, expense_id)
    if expense is None:
        raise HTTPException(status_code=404, detail="Expense not found")
    _check_expense_access(db, user, expense)
    comments = (
        db.query(Comment)
        .filter(Comment.expense_id == expense_id)
        .order_by(Comment.created_at.asc())
        .all()
    )
    return [
        {
            "id": c.id,
            "user_id": c.user_id,
            "text": c.text,
            "created_at": c.created_at.isoformat(),
        }
        for c in comments
    ]


@router.post("/{expense_id}/remind")
def remind_expense(
    expense_id: int,
    payload: CreateReminderRequest,
    user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    expense = db.get(Expense, expense_id)
    if expense is None:
        raise HTTPException(status_code=404, detail="Expense not found")
    _check_expense_access(db, user, expense)
    reminder = Reminder(
        expense_id=expense_id,
        from_user_id=user.id,
        to_user_id=payload.to_user_id,
        remind_date=payload.remind_date,
        message=payload.message,
    )
    db.add(reminder)
    target = db.get(User, payload.to_user_id)
    if target:
        notify(
            db,
            target.id,
            f"{user.name} reminded you about '{expense.description}'",
            f"expense/{expense_id}",
        )
    db.commit()
    return {"ok": True}


# ---------------------------------------------------------------- recurring ---


def _serialize_recurring(r: Recurring) -> dict:
    return {
        "id": r.id,
        "group_id": r.group_id,
        "description": r.description,
        "amount_cents": r.amount_cents,
        "currency": r.currency,
        "category": r.category,
        "frequency": r.frequency,
        "interval": r.interval,
        "start_date": r.start_date,
        "end_date": r.end_date,
        "split_method": r.split_method,
        "splits": r.splits,
        "payers": r.payers,
    }


def _next_occurrence(start_date: str, frequency: str, interval: int, n: int = 1) -> str:
    d = datetime.strptime(start_date, "%Y-%m-%d")
    if frequency == "daily":
        d += timedelta(days=interval)
    elif frequency == "weekly":
        d += timedelta(weeks=interval)
    elif frequency == "monthly":
        month = d.month - 1 + interval
        d = d.replace(year=d.year + month // 12, month=month % 12 + 1)
    elif frequency == "yearly":
        d = d.replace(year=d.year + interval)
    return d.strftime("%Y-%m-%d")


@recurring_router.post("")
def create_recurring(
    payload: CreateRecurringRequest,
    user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    if payload.group_id is not None:
        get_group_or_404(db, payload.group_id)
        if not is_group_member(db, payload.group_id, user.id):
            raise HTTPException(status_code=403, detail="Not a member of this group")
    amount_cents = round(payload.amount * 100)
    weights = payload.weights or [1.0] * len(payload.participants)
    splits = []
    if payload.split_method == "amounts":
        for uid, share in zip(payload.participants, payload.shares or []):
            splits.append({"user_id": uid, "share_cents": round(share * 100)})
    elif payload.split_method in ("percentages", "shares"):
        parts = distribute(amount_cents, payload.shares or [])
        for uid, part in zip(payload.participants, parts):
            splits.append({"user_id": uid, "share_cents": part})
    else:
        parts = distribute(amount_cents, weights)
        for uid, part in zip(payload.participants, parts):
            splits.append({"user_id": uid, "share_cents": part})

    recurring = Recurring(
        owner_user_id=user.id,
        group_id=payload.group_id,
        description=payload.description.strip(),
        amount_cents=amount_cents,
        currency=payload.currency,
        category=payload.category,
        frequency=payload.frequency,
        interval=payload.interval,
        start_date=payload.start_date or datetime.now().strftime("%Y-%m-%d"),
        end_date=payload.end_date,
        split_method=payload.split_method,
        splits=splits,
        payers=[{"user_id": user.id, "amount_cents": amount_cents}],
    )
    db.add(recurring)
    db.commit()
    db.refresh(recurring)
    return _serialize_recurring(recurring)


@recurring_router.get("")
def list_recurring(
    user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    items = db.query(Recurring).filter(Recurring.owner_user_id == user.id).all()
    return [_serialize_recurring(r) for r in items]


@recurring_router.delete("/{recurring_id}")
def delete_recurring(
    recurring_id: int,
    user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    r = db.get(Recurring, recurring_id)
    if r is None or r.owner_user_id != user.id:
        raise HTTPException(status_code=404, detail="Recurring expense not found")
    db.delete(r)
    db.commit()
    return {"ok": True}


@recurring_router.post("/{recurring_id}/create-next")
def create_next(
    recurring_id: int,
    user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    r = db.get(Recurring, recurring_id)
    if r is None or r.owner_user_id != user.id:
        raise HTTPException(status_code=404, detail="Recurring expense not found")
    if r.end_date and r.start_date > r.end_date:
        raise HTTPException(status_code=400, detail="Recurring series has ended")

    expense = Expense(
        group_id=r.group_id,
        description=r.description,
        amount_cents=r.amount_cents,
        currency=r.currency,
        expense_date=_next_occurrence(r.start_date, r.frequency, r.interval),
        category=r.category,
        created_by=user.id,
        is_recurring=True,
    )
    for payer in r.payers:
        expense.payers.append(
            ExpensePayer(user_id=payer["user_id"], amount_cents=payer["amount_cents"])
        )
    for split in r.splits:
        expense.participants.append(
            ExpenseParticipant(
                user_id=split["user_id"],
                share_cents=split["share_cents"],
                weight=1.0,
            )
        )
    r.start_date = _next_occurrence(r.start_date, r.frequency, r.interval)
    db.add(expense)
    db.commit()
    db.refresh(expense)
    return serialize_expense(expense)
