from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy.orm import Session

from ..database import get_db
from ..deps import get_current_user
from ..models import User
from ..schemas import ProRequest, UserUpdate
from ..service import serialize_user

router = APIRouter(prefix="/users", tags=["users"])


@router.get("/me")
def get_me(user: User = Depends(get_current_user)):
    return serialize_user(user)


@router.put("/me")
def update_me(
    payload: UserUpdate,
    user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    data = payload.model_dump(exclude_none=True)
    if "base_currency" in data:
        data["base_currency"] = data["base_currency"].upper()
    for key, value in data.items():
        setattr(user, key, value)
    db.commit()
    db.refresh(user)
    return serialize_user(user)


@router.get("/search")
def search_users(
    q: str,
    user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    if not q.strip():
        return []
    like = f"%{q.strip()}%"
    results = (
        db.query(User)
        .filter(
            User.id != user.id,
            (User.name.ilike(like) | User.email.ilike(like) | User.mobile.ilike(like)),
        )
        .limit(20)
        .all()
    )
    return [serialize_user(u) for u in results]


@router.get("/pro")
def get_pro_status(user: User = Depends(get_current_user)):
    return {"is_pro": user.is_pro, "features": _pro_features()}


@router.put("/pro")
def toggle_pro(
    payload: ProRequest,
    user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    user.is_pro = payload.enable
    db.commit()
    return {"is_pro": user.is_pro, "features": _pro_features()}


def _pro_features() -> list[str]:
    return [
        "currency_conversion",
        "receipt_ocr",
        "group_stats",
        "packing_lists",
        "advanced_activity_filters",
        "priority_support",
    ]
