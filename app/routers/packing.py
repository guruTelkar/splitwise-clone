from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy.orm import Session

from ..database import get_db
from ..deps import get_current_user
from ..models import PackingItem, User
from ..schemas import CreatePackingItemRequest
from ..service import is_group_member, group_member_ids
from ..notifications import notify_packing_item_added, notify_packing_item_toggled

router = APIRouter(prefix="/packing", tags=["packing"])


def _serialize(item: PackingItem) -> dict:
    return {
        "id": item.id,
        "group_id": item.group_id,
        "name": item.name,
        "assigned_to": item.assigned_to,
        "is_checked": item.is_checked,
        "created_at": item.created_at.isoformat(),
    }


@router.get("/group/{group_id}")
def list_items(
    group_id: int,
    user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    if not is_group_member(db, group_id, user.id):
        raise HTTPException(status_code=403, detail="Not a member of this group")
    items = (
        db.query(PackingItem)
        .filter(PackingItem.group_id == group_id)
        .order_by(PackingItem.id.asc())
        .all()
    )
    return [_serialize(i) for i in items]


@router.post("/group/{group_id}")
def add_item(
    group_id: int,
    payload: CreatePackingItemRequest,
    user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    if not is_group_member(db, group_id, user.id):
        raise HTTPException(status_code=403, detail="Not a member of this group")
    item = PackingItem(
        group_id=group_id,
        name=payload.name.strip(),
        assigned_to=payload.assigned_to,
    )
    db.add(item)
    db.commit()
    db.refresh(item)
    # Send email/SMS notification
    from ..models import Group
    grp = db.get(Group, group_id)
    member_ids_list = group_member_ids(db, group_id)
    members = []
    for uid in member_ids_list:
        u = db.get(User, uid)
        if u and u.email:
            members.append({"email": u.email})
    if grp:
        notify_packing_item_added(grp.name, item.name, members)
    return _serialize(item)


@router.put("/{item_id}")
def toggle_item(
    item_id: int,
    user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    item = db.get(PackingItem, item_id)
    if item is None:
        raise HTTPException(status_code=404, detail="Item not found")
    if not is_group_member(db, item.group_id, user.id):
        raise HTTPException(status_code=403, detail="Not a member of this group")
    item.is_checked = not item.is_checked
    db.commit()
    # Send email/SMS notification
    from ..models import Group
    grp = db.get(Group, item.group_id)
    member_ids_list = group_member_ids(db, item.group_id)
    members = [{"email": u.email} for uid in member_ids_list if (u := db.get(User, uid)) and u.email]
    if grp:
        notify_packing_item_toggled(grp.name, item.name, item.is_checked, user.name, members)
    return _serialize(item)


@router.delete("/{item_id}")
def delete_item(
    item_id: int,
    user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    item = db.get(PackingItem, item_id)
    if item is None:
        raise HTTPException(status_code=404, detail="Item not found")
    if not is_group_member(db, item.group_id, user.id):
        raise HTTPException(status_code=403, detail="Not a member of this group")
    db.delete(item)
    db.commit()
    return {"ok": True}
