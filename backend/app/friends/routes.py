"""
Friends routes:
  GET    /friends       — list this user's friends
  POST   /friends       — add a friend by phone or email
  DELETE /friends/{id}  — remove a friendship
"""

from __future__ import annotations

import uuid
from typing import Optional

from fastapi import APIRouter, Depends, HTTPException, status
from pydantic import BaseModel, EmailStr, Field
from sqlalchemy.orm import Session

from app.auth.deps import get_current_verified_user
from app.database import get_db
from app.friends.service import add_friend, list_friends, remove_friend
from app.models import User


router = APIRouter(prefix="/friends", tags=["friends"])


# ---------- Schemas ----------

class AddFriendRequest(BaseModel):
    phone: Optional[str] = Field(default=None, max_length=32)
    email: Optional[EmailStr] = None
    display_name: Optional[str] = Field(default=None, max_length=120)


class FriendOut(BaseModel):
    id: str
    status: str                      # "pending" | "active"
    display_name: Optional[str]
    # Set when the friend has an account
    friend_user_id: Optional[str]
    friend_email: Optional[str]
    friend_phone: Optional[str]
    # Set when the friend hasn't signed up yet
    pending_email: Optional[str]
    pending_phone: Optional[str]


# ---------- Helpers ----------

def _to_out(f, db: Session) -> FriendOut:
    friend_user = None
    if f.friend_id:
        friend_user = db.query(User).filter(User.id == f.friend_id).one_or_none()
    return FriendOut(
        id=str(f.id),
        status=f.status,
        display_name=f.display_name,
        friend_user_id=str(friend_user.id) if friend_user else None,
        friend_email=friend_user.email if friend_user else None,
        friend_phone=friend_user.phone if friend_user else None,
        pending_email=f.pending_email,
        pending_phone=f.pending_phone,
    )


# ---------- Routes ----------

@router.get("", response_model=list[FriendOut])
def get_friends(
    user: User = Depends(get_current_verified_user),
    db: Session = Depends(get_db),
):
    return [_to_out(f, db) for f in list_friends(db, owner=user)]


@router.post("", response_model=FriendOut, status_code=status.HTTP_201_CREATED)
def post_friend(
    payload: AddFriendRequest,
    user: User = Depends(get_current_verified_user),
    db: Session = Depends(get_db),
):
    try:
        friendship = add_friend(
            db,
            owner=user,
            phone=payload.phone,
            email=payload.email,
            display_name=payload.display_name,
        )
        db.commit()
        return _to_out(friendship, db)
    except ValueError as e:
        db.rollback()
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail=str(e))


@router.delete("/{friendship_id}", status_code=status.HTTP_204_NO_CONTENT)
def delete_friend(
    friendship_id: uuid.UUID,
    user: User = Depends(get_current_verified_user),
    db: Session = Depends(get_db),
):
    ok = remove_friend(db, owner=user, friendship_id=friendship_id)
    if not ok:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Friend not found")
    db.commit()
