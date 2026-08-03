from fastapi import APIRouter, Depends
from sqlalchemy.orm import Session

from ..database import get_db
from ..deps import get_current_user
from ..models import Notification, Reminder, User

router = APIRouter(prefix="/notifications", tags=["notifications"])
reminder_router = APIRouter(prefix="/reminders", tags=["reminders"])


@router.get("")
def list_notifications(
    user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    items = (
        db.query(Notification)
        .filter(Notification.user_id == user.id)
        .order_by(Notification.created_at.desc())
        .limit(100)
        .all()
    )
    return [
        {
            "id": n.id,
            "message": n.message,
            "link": n.link,
            "is_read": n.is_read,
            "created_at": n.created_at.isoformat(),
        }
        for n in items
    ]


@router.put("/{notification_id}/read")
def mark_read(
    notification_id: int,
    user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    n = db.get(Notification, notification_id)
    if n and n.user_id == user.id:
        n.is_read = True
        db.commit()
    return {"ok": True}


@reminder_router.get("")
def list_reminders(
    user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    items = (
        db.query(Reminder)
        .filter(
            (Reminder.to_user_id == user.id) | (Reminder.from_user_id == user.id)
        )
        .order_by(Reminder.remind_date.desc())
        .all()
    )
    return [
        {
            "id": r.id,
            "expense_id": r.expense_id,
            "from_user_id": r.from_user_id,
            "to_user_id": r.to_user_id,
            "remind_date": r.remind_date,
            "message": r.message,
            "created_at": r.created_at.isoformat(),
        }
        for r in items
    ]
