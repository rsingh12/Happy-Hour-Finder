"""
Outings service: create, invite, respond, list, get with member status.
"""

from __future__ import annotations

import uuid
from datetime import date as dt_date, datetime, time as dt_time, timezone
from typing import Optional

from sqlalchemy import or_
from sqlalchemy.orm import Session, joinedload

from app.friends.service import find_user_by_contact, normalize_email, normalize_phone
from app.models import HappyHour, Outing, OutingMember, User, Venue


# ---------- Outing creation ----------

def create_outing(
    db: Session,
    *,
    organizer: User,
    venue_id: uuid.UUID,
    happy_hour_id: Optional[uuid.UUID],
    outing_date: dt_date,
    start_time: dt_time,
    end_time: dt_time,
) -> Outing:
    venue = db.query(Venue).filter(Venue.id == venue_id).one_or_none()
    if not venue:
        raise ValueError("Venue not found.")

    if happy_hour_id:
        hh = db.query(HappyHour).filter(HappyHour.id == happy_hour_id).one_or_none()
        if not hh or hh.venue_id != venue.id:
            raise ValueError("Happy hour does not belong to that venue.")

    if outing_date < dt_date.today():
        raise ValueError("Outing date can't be in the past.")

    if start_time >= end_time:
        # Allow midnight wrap (e.g., 22:00 - 01:00) only if end is in the wee hours
        if not (end_time < dt_time(6, 0)):
            raise ValueError("End time must be after start time.")

    outing = Outing(
        organizer_id=organizer.id,
        venue_id=venue.id,
        happy_hour_id=happy_hour_id,
        outing_date=outing_date,
        start_time=start_time,
        end_time=end_time,
    )
    db.add(outing)
    db.flush()

    # Organizer auto-joins as accepted
    db.add(OutingMember(
        outing_id=outing.id,
        user_id=organizer.id,
        status="accepted",
        responded_at=datetime.now(timezone.utc),
    ))
    db.flush()
    return outing


# ---------- Inviting ----------

def invite_to_outing(
    db: Session,
    *,
    organizer: User,
    outing: Outing,
    user_ids: Optional[list[uuid.UUID]] = None,
    contacts: Optional[list[dict]] = None,
) -> dict:
    """
    Invite users to an outing.

    user_ids — accounts the organizer is already friends with
    contacts — list of {phone, email} for ad-hoc invites; if the contact
               has an account we invite them, else we record the
               invitation as pending (caller should send an email invite
               to download the app).

    Only the organizer can invite.

    Returns a dict with `invited_user_ids` and `pending_contacts` for
    callers (the route layer) to fire push/email notifications.
    """
    if outing.organizer_id != organizer.id:
        raise PermissionError("Only the organizer can invite people to an outing.")

    invited_user_ids: list[uuid.UUID] = []
    pending_contacts: list[dict] = []

    # Resolve user_ids
    if user_ids:
        for uid in user_ids:
            u = db.query(User).filter(User.id == uid).one_or_none()
            if not u or u.id == organizer.id:
                continue
            _ensure_member(db, outing, u, status="invited")
            invited_user_ids.append(u.id)

    # Resolve contacts
    if contacts:
        for c in contacts:
            phone = normalize_phone(c.get("phone"))
            email = normalize_email(c.get("email"))
            if not phone and not email:
                continue
            other = find_user_by_contact(db, phone=phone, email=email)
            if other and other.id != organizer.id:
                _ensure_member(db, outing, other, status="invited")
                if other.id not in invited_user_ids:
                    invited_user_ids.append(other.id)
            else:
                # No account yet — caller should send email/SMS invite
                pending_contacts.append({"email": email, "phone": phone})

    db.flush()
    return {
        "invited_user_ids": invited_user_ids,
        "pending_contacts": pending_contacts,
    }


def _ensure_member(
    db: Session, outing: Outing, user: User, *, status: str
) -> OutingMember:
    """Idempotent: if a member already exists, leave it; else create with given status."""
    existing = (
        db.query(OutingMember)
        .filter(OutingMember.outing_id == outing.id, OutingMember.user_id == user.id)
        .one_or_none()
    )
    if existing:
        return existing
    member = OutingMember(
        outing_id=outing.id,
        user_id=user.id,
        status=status,
    )
    db.add(member)
    db.flush()
    return member


# ---------- Responding ----------

def respond_to_outing(
    db: Session,
    *,
    user: User,
    outing: Outing,
    response: str,  # "accepted" | "declined"
) -> OutingMember:
    if response not in ("accepted", "declined"):
        raise ValueError("response must be 'accepted' or 'declined'")

    member = (
        db.query(OutingMember)
        .filter(OutingMember.outing_id == outing.id, OutingMember.user_id == user.id)
        .one_or_none()
    )
    if not member:
        raise PermissionError("You weren't invited to this outing.")
    if member.status == "accepted" and response == "accepted":
        return member  # idempotent

    member.status = response
    member.responded_at = datetime.now(timezone.utc)
    db.flush()
    return member


# ---------- Reading ----------

def get_outing_with_members(db: Session, outing_id: uuid.UUID) -> Optional[Outing]:
    return (
        db.query(Outing)
        .options(joinedload(Outing.members))
        .filter(Outing.id == outing_id)
        .one_or_none()
    )


def list_outings_for_user(db: Session, user: User) -> list[Outing]:
    """Outings where the user is the organizer OR an invited/accepted member."""
    member_outing_ids = db.query(OutingMember.outing_id).filter(
        OutingMember.user_id == user.id
    ).subquery()

    return (
        db.query(Outing)
        .filter(
            or_(
                Outing.organizer_id == user.id,
                Outing.id.in_(member_outing_ids),
            )
        )
        .order_by(Outing.outing_date.desc(), Outing.start_time.desc())
        .all()
    )
