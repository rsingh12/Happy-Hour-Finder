"""
Friends service: shared logic between routes and the auth-register
reconciliation hook.

A friend record links one user (user_id, the owner) to either:
  - an existing user (friend_id set, status=active), or
  - a pending phone/email contact who hasn't signed up yet
    (pending_phone or pending_email set, status=pending).

When a new user registers, we look for pending records matching their
email/phone and promote them to active.
"""

from __future__ import annotations

import re
import uuid
from typing import Optional

from sqlalchemy.orm import Session

from app.models import Friend, User


def normalize_phone(phone: Optional[str]) -> Optional[str]:
    """Strip everything but digits and leading +. Returns None if empty."""
    if not phone:
        return None
    s = phone.strip()
    if not s:
        return None
    # Keep leading +, drop other non-digits
    leading_plus = s.startswith("+")
    digits = re.sub(r"\D", "", s)
    if not digits:
        return None
    return ("+" + digits) if leading_plus else digits


def normalize_email(email: Optional[str]) -> Optional[str]:
    if not email:
        return None
    s = email.strip().lower()
    return s or None


def find_user_by_contact(
    db: Session,
    *,
    phone: Optional[str] = None,
    email: Optional[str] = None,
) -> Optional[User]:
    """Return the User whose phone or email matches, or None."""
    if email:
        u = db.query(User).filter(User.email == email).one_or_none()
        if u:
            return u
    if phone:
        u = db.query(User).filter(User.phone == phone).one_or_none()
        if u:
            return u
    return None


def add_friend(
    db: Session,
    *,
    owner: User,
    phone: Optional[str] = None,
    email: Optional[str] = None,
    display_name: Optional[str] = None,
) -> Friend:
    """
    Add a friend by phone or email. If the contact has an account,
    creates an active friendship; else creates a pending one.

    Raises ValueError on bad input or duplicate friendships.
    """
    phone = normalize_phone(phone)
    email = normalize_email(email)
    if not phone and not email:
        raise ValueError("Provide at least one of: phone, email")

    if email == owner.email or (phone and phone == (owner.phone or "")):
        raise ValueError("Can't add yourself as a friend.")

    other = find_user_by_contact(db, phone=phone, email=email)

    if other:
        # Active friendship — check for duplicate
        existing = (
            db.query(Friend)
            .filter(Friend.user_id == owner.id, Friend.friend_id == other.id)
            .one_or_none()
        )
        if existing:
            raise ValueError(f"{other.email} is already in your friends list.")

        friendship = Friend(
            user_id=owner.id,
            friend_id=other.id,
            display_name=display_name or other.display_name,
            status="active",
        )
    else:
        # Pending — check we don't already have a pending record for this contact
        q = db.query(Friend).filter(Friend.user_id == owner.id, Friend.status == "pending")
        if email:
            q_email = q.filter(Friend.pending_email == email).one_or_none()
            if q_email:
                raise ValueError(f"You already have a pending invite for {email}.")
        if phone:
            q_phone = q.filter(Friend.pending_phone == phone).one_or_none()
            if q_phone:
                raise ValueError(f"You already have a pending invite for {phone}.")

        friendship = Friend(
            user_id=owner.id,
            friend_id=None,
            pending_email=email,
            pending_phone=phone,
            display_name=display_name,
            status="pending",
        )

    db.add(friendship)
    db.flush()
    return friendship


def list_friends(db: Session, *, owner: User) -> list[Friend]:
    return (
        db.query(Friend)
        .filter(Friend.user_id == owner.id)
        .order_by(Friend.created_at.desc())
        .all()
    )


def remove_friend(db: Session, *, owner: User, friendship_id: uuid.UUID) -> bool:
    f = (
        db.query(Friend)
        .filter(Friend.id == friendship_id, Friend.user_id == owner.id)
        .one_or_none()
    )
    if not f:
        return False
    db.delete(f)
    db.flush()
    return True


def reconcile_pending_for_new_user(db: Session, new_user: User) -> int:
    """
    When a user signs up, find pending friend records that match their
    email or phone and promote them to active.

    Returns the number of records reconciled.
    """
    if not new_user.email and not new_user.phone:
        return 0

    q = db.query(Friend).filter(Friend.status == "pending")
    matches = []
    if new_user.email:
        matches += (
            q.filter(Friend.pending_email == new_user.email).all()
        )
    if new_user.phone:
        matches += (
            q.filter(Friend.pending_phone == new_user.phone).all()
        )

    seen = set()
    reconciled = 0
    for f in matches:
        if f.id in seen:
            continue
        seen.add(f.id)
        f.friend_id = new_user.id
        f.status = "active"
        f.pending_email = None
        f.pending_phone = None
        reconciled += 1

    if reconciled:
        db.flush()
    return reconciled
