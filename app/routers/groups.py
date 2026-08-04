import secrets

from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy.orm import Session

from ..balance import balance_for_expenses, simplify_debts
from ..database import get_db
from ..deps import get_current_user
from ..models import Expense, Group, GroupInvite, GroupMember, Payment, User
from ..schemas import AddMemberRequest, CreateGroupRequest, JoinGroupRequest, UpdateGroupRequest
from ..service import (
    create_activity,
    get_group_or_404,
    group_member_ids,
    is_group_member,
    notify,
    serialize_expense,
    serialize_user,
)
from ..notifications import notify_group_created, notify_member_joined

router = APIRouter(prefix="/groups", tags=["groups"])


def _group_balance(db: Session, group_id: int) -> dict[int, int]:
    expenses = db.query(Expense).filter(Expense.group_id == group_id).all()
    payments = db.query(Payment).filter(Payment.group_id == group_id).all()
    return balance_for_expenses(expenses, payments)


def _serialize_group(db: Session, group: Group, user: User) -> dict:
    members = [
        db.get(User, m.user_id) for m in group.members if db.get(User, m.user_id)
    ]
    net = _group_balance(db, group.id)
    my_balance = net.get(user.id, 0)
    total_balance = sum(abs(v) for v in net.values()) / 2
    return {
        "id": group.id,
        "name": group.name,
        "description": group.description,
        "group_type": group.group_type,
        "currency": group.currency,
        "simplify_debts": group.simplify_debts,
        "is_archived": group.is_archived,
        "created_at": group.created_at.isoformat(),
        "members": [serialize_user(m) for m in members],
        "my_balance_cents": my_balance,
        "total_balance_cents": round(total_balance),
        "expense_count": (
            db.query(Expense).filter(Expense.group_id == group.id).count()
        ),
    }


@router.post("")
def create_group(
    payload: CreateGroupRequest,
    user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    group = Group(
        name=payload.name,
        description=payload.description,
        group_type=payload.group_type,
        currency=payload.currency,
        simplify_debts=payload.simplify_debts,
        created_by=user.id,
    )
    db.add(group)
    db.flush()
    member_ids = set(payload.member_ids) | {user.id}
    for uid in member_ids:
        if db.get(User, uid) is None:
            raise HTTPException(status_code=404, detail=f"User {uid} not found")
        db.add(GroupMember(group_id=group.id, user_id=uid))
    db.commit()
    db.refresh(group)
    create_activity(db, group.id, user.id, "group_created", {"name": group.name})
    db.commit()
    # Send email/SMS notifications
    members = []
    for uid in member_ids:
        u = db.get(User, uid)
        if u and u.email:
            members.append({"email": u.email, "mobile": u.mobile})
    notify_group_created(group.name, members, user.name)
    return _serialize_group(db, group, user)


@router.get("")
def list_groups(
    user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    membership = (
        db.query(GroupMember.group_id).filter(GroupMember.user_id == user.id).all()
    )
    group_ids = [g[0] for g in membership]
    groups = (
        db.query(Group).filter(Group.id.in_(group_ids)).order_by(Group.updated_at.desc()).all()
    )
    return [_serialize_group(db, g, user) for g in groups]


@router.get("/invites/{code}")
def invite_info(code: str, db: Session = Depends(get_db)):
    """Public preview used before joining a group via an invite link."""
    invite = db.query(GroupInvite).filter(GroupInvite.code == code.strip()).first()
    if invite is None:
        raise HTTPException(status_code=404, detail="Invite not found")
    group = db.get(Group, invite.group_id)
    if group is None or group.is_archived:
        raise HTTPException(status_code=404, detail="Group not found")
    member_count = (
        db.query(GroupMember).filter(GroupMember.group_id == group.id).count()
    )
    return {
        "code": code.strip(),
        "group": {
            "id": group.id,
            "name": group.name,
            "description": group.description,
            "member_count": member_count,
        },
    }


@router.post("/{group_id}/invite")
def create_invite(
    group_id: int,
    user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    group = get_group_or_404(db, group_id)
    if not is_group_member(db, group_id, user.id):
        raise HTTPException(status_code=403, detail="Not a member of this group")
    existing = (
        db.query(GroupInvite).filter(GroupInvite.group_id == group_id).first()
    )
    if existing:
        return {"code": existing.code}
    code = secrets.token_urlsafe(6)
    db.add(GroupInvite(group_id=group_id, code=code, created_by=user.id))
    db.commit()
    return {"code": code}


@router.post("/join")
def join_group(
    payload: JoinGroupRequest,
    user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    invite = (
        db.query(GroupInvite).filter(GroupInvite.code == payload.code.strip()).first()
    )
    if invite is None:
        raise HTTPException(status_code=404, detail="Invite not found")
    group = db.get(Group, invite.group_id)
    if group is None or group.is_archived:
        raise HTTPException(status_code=404, detail="Group not found")
    if not is_group_member(db, group.id, user.id):
        db.add(GroupMember(group_id=group.id, user_id=user.id))
        db.commit()
        create_activity(db, group.id, user.id, "member_joined", {"name": user.name})
        db.commit()
        # Notify existing members about new joiner
        member_ids_list = group_member_ids(db, group.id)
        members = [{"email": u.email} for uid in member_ids_list if (u := db.get(User, uid)) and u.email and u.id != user.id]
        notify_member_joined(group.name, user.name, members)
    return _serialize_group(db, group, user)


@router.get("/{group_id}")
def get_group(
    group_id: int,
    user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    group = get_group_or_404(db, group_id)
    if not is_group_member(db, group_id, user.id):
        raise HTTPException(status_code=403, detail="Not a member of this group")
    expenses = (
        db.query(Expense)
        .filter(Expense.group_id == group_id)
        .order_by(Expense.expense_date.desc(), Expense.id.desc())
        .all()
    )
    payments = (
        db.query(Payment)
        .filter(Payment.group_id == group_id)
        .order_by(Payment.created_at.desc())
        .all()
    )
    return {
        **_serialize_group(db, group, user),
        "expenses": [serialize_expense(e) for e in expenses],
        "payments": [
            {
                "id": p.id,
                "from_user_id": p.from_user_id,
                "to_user_id": p.to_user_id,
                "amount_cents": p.amount_cents,
                "currency": p.currency,
                "note": p.note,
                "created_at": p.created_at.isoformat(),
            }
            for p in payments
        ],
    }


@router.put("/{group_id}")
def update_group(
    group_id: int,
    payload: UpdateGroupRequest,
    user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    group = get_group_or_404(db, group_id)
    if not is_group_member(db, group_id, user.id):
        raise HTTPException(status_code=403, detail="Not a member of this group")
    for key, value in payload.model_dump(exclude_none=True).items():
        setattr(group, key, value)
    db.commit()
    db.refresh(group)
    return _serialize_group(db, group, user)


@router.delete("/{group_id}")
def delete_group(
    group_id: int,
    user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    group = get_group_or_404(db, group_id)
    if group.created_by != user.id:
        raise HTTPException(status_code=403, detail="Only the group creator can delete it")
    db.delete(group)
    db.commit()
    return {"ok": True}


@router.post("/{group_id}/members")
def add_member(
    group_id: int,
    payload: AddMemberRequest,
    user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    group = get_group_or_404(db, group_id)
    if not is_group_member(db, group_id, user.id):
        raise HTTPException(status_code=403, detail="Not a member of this group")
    if db.get(User, payload.user_id) is None:
        raise HTTPException(status_code=404, detail="User not found")
    if is_group_member(db, group_id, payload.user_id):
        raise HTTPException(status_code=400, detail="Already a member")
    db.add(GroupMember(group_id=group_id, user_id=payload.user_id))
    db.commit()
    create_activity(
        db, group_id, user.id, "member_added", {"user_id": payload.user_id}
    )
    db.commit()
    return _serialize_group(db, group, user)


@router.delete("/{group_id}/members/{member_id}")
def remove_member(
    group_id: int,
    member_id: int,
    user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    group = get_group_or_404(db, group_id)
    if not is_group_member(db, group_id, user.id):
        raise HTTPException(status_code=403, detail="Not a member of this group")
    member = (
        db.query(GroupMember)
        .filter(GroupMember.group_id == group_id, GroupMember.user_id == member_id)
        .first()
    )
    if member is None:
        raise HTTPException(status_code=404, detail="Member not found")
    if member_id == group.created_by:
        raise HTTPException(status_code=400, detail="Cannot remove the group creator")
    db.delete(member)
    db.commit()
    return _serialize_group(db, group, user)


@router.get("/{group_id}/balances")
def group_balances(
    group_id: int,
    user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    group = get_group_or_404(db, group_id)
    if not is_group_member(db, group_id, user.id):
        raise HTTPException(status_code=403, detail="Not a member of this group")
    member_ids = group_member_ids(db, group_id)
    net = _group_balance(db, group_id)
    simplified = simplify_debts(net) if group.simplify_debts else None
    members = {m.id: serialize_user(m) for m in db.query(User).filter(User.id.in_(member_ids))}
    balance_rows = [
        {"user": members[uid], "balance_cents": net.get(uid, 0)} for uid in member_ids
    ]
    balance_rows.sort(key=lambda r: -r["balance_cents"])
    return {
        "group_id": group_id,
        "simplified_debts": simplified,
        "members": balance_rows,
    }


@router.get("/{group_id}/expenses")
def list_group_expenses(
    group_id: int,
    user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    group = get_group_or_404(db, group_id)
    if not is_group_member(db, group_id, user.id):
        raise HTTPException(status_code=403, detail="Not a member of this group")
    expenses = (
        db.query(Expense)
        .filter(Expense.group_id == group_id)
        .order_by(Expense.expense_date.desc(), Expense.id.desc())
        .all()
    )
    return [serialize_expense(e) for e in expenses]
