from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy.orm import Session

from ..balance import balance_for_expenses
from ..database import get_db
from ..deps import get_current_user
from ..models import Expense, FriendLink, Payment, User
from ..schemas import AddFriendRequest
from ..security import PLACEHOLDER_PASSWORD
from ..service import serialize_expense, serialize_user

router = APIRouter(prefix="/friends", tags=["friends"])


def _resolve_friend(
    db: Session, me: User, friend_id: int | None, email: str | None, mobile: str | None, name: str | None
) -> User:
    if friend_id is not None:
        friend = db.get(User, friend_id)
        if friend is None:
            raise HTTPException(status_code=404, detail="User not found")
        return friend
    if mobile:
        friend = db.query(User).filter(User.mobile == mobile.strip()).first()
        if friend is not None:
            return friend
    email = email.lower().strip() if email else None
    if email:
        friend = db.query(User).filter(User.email == email).first()
    else:
        friend = None
    if friend is None:
        display = (name or email or "Unknown").strip() or "Unknown"
        friend = User(
            email=email or f"placeholder_{mobile}@splitwise.local",
            name=display,
            password_hash=PLACEHOLDER_PASSWORD,
            base_currency=me.base_currency,
            mobile=mobile.strip() if mobile else None,
        )
        db.add(friend)
        db.flush()
    return friend


def _friend_balance(db: Session, me_id: int, friend_id: int) -> dict[int, int]:
    """Net balances from all non-group (IOU) expenses shared by two users."""
    expenses = (
        db.query(Expense)
        .filter(Expense.group_id.is_(None))
        .all()
    )
    relevant = []
    for e in expenses:
        participant_ids = {p.user_id for p in e.participants}
        payer_ids = {p.user_id for p in e.payers}
        if me_id in participant_ids and friend_id in participant_ids:
            relevant.append(e)
    payments = (
        db.query(Payment)
        .filter(Payment.group_id.is_(None))
        .all()
    )
    rel_payments = [
        p
        for p in payments
        if {p.from_user_id, p.to_user_id} == {me_id, friend_id}
    ]
    return balance_for_expenses(relevant, rel_payments)


@router.get("")
def list_friends(
    user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    links = (
        db.query(FriendLink)
        .filter((FriendLink.user_id == user.id) | (FriendLink.friend_id == user.id))
        .all()
    )
    friend_ids = {
        l.friend_id if l.user_id == user.id else l.user_id for l in links
    }
    result = []
    for fid in friend_ids:
        friend = db.get(User, fid)
        if friend is None:
            continue
        net = _friend_balance(db, user.id, fid)
        result.append(
            {
                "friend": serialize_user(friend),
                "balance_cents": net.get(user.id, 0),
            }
        )
    result.sort(key=lambda r: r["friend"]["name"].lower())
    return result


@router.post("")
def add_friend(
    payload: AddFriendRequest,
    user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    friend = _resolve_friend(db, user, payload.friend_id, payload.email, payload.mobile, payload.name)
    if friend.id == user.id:
        raise HTTPException(status_code=400, detail="You cannot add yourself as a friend")
    existing = (
        db.query(FriendLink)
        .filter(
            (
                (FriendLink.user_id == user.id) & (FriendLink.friend_id == friend.id)
            )
            | (
                (FriendLink.user_id == friend.id) & (FriendLink.friend_id == user.id)
            )
        )
        .first()
    )
    if existing:
        db.commit()
        return {"friend": serialize_user(friend), "balance_cents": 0}
    db.add(FriendLink(user_id=user.id, friend_id=friend.id))
    db.commit()
    db.refresh(friend)
    return {"friend": serialize_user(friend), "balance_cents": 0}


@router.delete("/{friend_id}")
def remove_friend(
    friend_id: int,
    user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    link = (
        db.query(FriendLink)
        .filter(
            ((FriendLink.user_id == user.id) & (FriendLink.friend_id == friend_id))
            | ((FriendLink.user_id == friend_id) & (FriendLink.friend_id == user.id))
        )
        .first()
    )
    if link is None:
        raise HTTPException(status_code=404, detail="Not friends")
    db.delete(link)
    db.commit()
    return {"ok": True}


@router.get("/{friend_id}")
def get_friend(
    friend_id: int,
    user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    friend = db.get(User, friend_id)
    if friend is None:
        raise HTTPException(status_code=404, detail="User not found")
    net = _friend_balance(db, user.id, friend_id)
    return {
        "friend": serialize_user(friend),
        "balance_cents": net.get(user.id, 0),
    }


@router.get("/{friend_id}/expenses")
def friend_expenses(
    friend_id: int,
    user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    expenses = db.query(Expense).filter(Expense.group_id.is_(None)).all()
    result = []
    for e in expenses:
        participant_ids = {p.user_id for p in e.participants}
        if user.id in participant_ids and friend_id in participant_ids:
            result.append(serialize_expense(e))
    result.sort(key=lambda x: x["date"], reverse=True)
    return result
