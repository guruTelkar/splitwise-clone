from fastapi import APIRouter, Depends
from sqlalchemy.orm import Session

from ..database import get_db
from ..deps import get_current_user
from ..models import ActivityEvent, GroupMember, User

router = APIRouter(prefix="/activity", tags=["activity"])


@router.get("")
def get_activity(
    user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    my_group_ids = [
        g[0]
        for g in db.query(GroupMember.group_id).filter(GroupMember.user_id == user.id)
    ]
    events = (
        db.query(ActivityEvent)
        .order_by(ActivityEvent.created_at.desc())
        .limit(200)
        .all()
    )
    result = []
    for e in events:
        if e.group_id is None:
            continue
        if e.group_id not in my_group_ids:
            continue
        actor = db.get(User, e.actor_id)
        result.append(
            {
                "id": e.id,
                "group_id": e.group_id,
                "actor": {"id": actor.id, "name": actor.name} if actor else None,
                "event_type": e.event_type,
                "payload": e.payload,
                "created_at": e.created_at.isoformat(),
            }
        )
    return result
