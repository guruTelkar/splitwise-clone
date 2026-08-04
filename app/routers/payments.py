from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy.orm import Session

from ..database import get_db
from ..deps import get_current_user
from ..models import Group, Payment, User
from ..schemas import CreatePaymentRequest
from ..service import create_activity, is_group_member, notify
from ..notifications import notify_payment_recorded

router = APIRouter(prefix="/payments", tags=["payments"])


def _serialize(p: Payment) -> dict:
    return {
        "id": p.id,
        "group_id": p.group_id,
        "from_user_id": p.from_user_id,
        "to_user_id": p.to_user_id,
        "amount_cents": p.amount_cents,
        "currency": p.currency,
        "note": p.note,
        "created_at": p.created_at.isoformat(),
    }


def _create_payment(
    db: Session,
    user: User,
    payload: CreatePaymentRequest,
    group_id: int | None,
):
    if group_id is not None:
        group = db.get(Group, group_id)
        if group is None:
            raise HTTPException(status_code=404, detail="Group not found")
        if not is_group_member(db, group_id, user.id):
            raise HTTPException(status_code=403, detail="Not a member of this group")
        for uid in (payload.from_user_id, payload.to_user_id):
            if not is_group_member(db, group_id, uid):
                raise HTTPException(
                    status_code=400, detail="Both parties must be group members"
                )
    payment = Payment(
        group_id=group_id,
        from_user_id=payload.from_user_id,
        to_user_id=payload.to_user_id,
        amount_cents=round(payload.amount * 100),
        currency=payload.currency,
        note=payload.note,
    )
    db.add(payment)
    db.commit()
    db.refresh(payment)

    payer = db.get(User, payload.from_user_id)
    payee = db.get(User, payload.to_user_id)
    create_activity(
        db,
        group_id,
        user.id,
        "payment",
        {
            "payment_id": payment.id,
            "from_user_id": payload.from_user_id,
            "to_user_id": payload.to_user_id,
            "amount_cents": payment.amount_cents,
            "currency": payment.currency,
        },
    )
    if payee and payee.id != user.id:
        notify(
            db,
            payee.id,
            f"{payer.name if payer else 'Someone'} paid you {payment.amount_cents / 100:.2f} {payment.currency}",
            f"payment/{payment.id}",
        )
    db.commit()
    # Send email/SMS notification
    if payer and payee and payer.email and payee.email:
        notify_payment_recorded(
            payer.name or "Someone", payee.name or "Someone",
            payment.amount_cents, payment.currency, payer.email, payee.email,
        )
    return _serialize(payment)


@router.post("")
def create_friend_payment(
    payload: CreatePaymentRequest,
    user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    return _create_payment(db, user, payload, group_id=None)


@router.get("")
def list_friend_payments(
    user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    payments = (
        db.query(Payment)
        .filter(
            Payment.group_id.is_(None),
            (Payment.from_user_id == user.id) | (Payment.to_user_id == user.id),
        )
        .order_by(Payment.created_at.desc())
        .all()
    )
    return [_serialize(p) for p in payments]


@router.post("/group/{group_id}")
def create_group_payment(
    group_id: int,
    payload: CreatePaymentRequest,
    user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    return _create_payment(db, user, payload, group_id=group_id)


@router.get("/group/{group_id}")
def list_group_payments(
    group_id: int,
    user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    if not is_group_member(db, group_id, user.id):
        raise HTTPException(status_code=403, detail="Not a member of this group")
    payments = (
        db.query(Payment)
        .filter(Payment.group_id == group_id)
        .order_by(Payment.created_at.desc())
        .all()
    )
    return [_serialize(p) for p in payments]
