"""
Outings routes:
  POST   /outings                 — create an outing (auto-joins organizer)
  GET    /outings                 — list outings the user is involved in
  GET    /outings/{id}            — outing detail with members
  POST   /outings/{id}/invite     — organizer invites users / contacts
  POST   /outings/{id}/respond    — invitee accepts or declines
  GET    /outings/suggest         — density-based date suggestion
"""

from __future__ import annotations

import uuid
from datetime import date as dt_date, time as dt_time
from typing import Optional

from fastapi import APIRouter, BackgroundTasks, Depends, HTTPException, Query, status
from pydantic import BaseModel, EmailStr, Field
from sqlalchemy.orm import Session

from app.auth.deps import get_current_verified_user
from app.database import get_db
from app.models import HappyHour, Outing, OutingMember, User, Venue
from app.outings.service import (
    create_outing,
    get_outing_with_members,
    invite_to_outing,
    list_outings_for_user,
    respond_to_outing,
)
from app.outings.suggester import suggest_outing


router = APIRouter(prefix="/outings", tags=["outings"])


# ---------- Schemas ----------

class CreateOutingRequest(BaseModel):
    venue_id: uuid.UUID
    happy_hour_id: Optional[uuid.UUID] = None
    outing_date: dt_date
    start_time: str = Field(description="HH:MM 24-hour")
    end_time: str = Field(description="HH:MM 24-hour")


class InviteContact(BaseModel):
    phone: Optional[str] = None
    email: Optional[EmailStr] = None


class InviteRequest(BaseModel):
    user_ids: Optional[list[uuid.UUID]] = None
    contacts: Optional[list[InviteContact]] = None


class RespondRequest(BaseModel):
    response: str = Field(description="'accepted' or 'declined'")


class MemberOut(BaseModel):
    user_id: str
    display_name: Optional[str]
    email: Optional[str]
    phone: Optional[str]
    status: str
    invited_at: str
    responded_at: Optional[str]


class OutingOut(BaseModel):
    id: str
    organizer_id: str
    venue_id: str
    venue_name: str
    venue_address: Optional[str]
    happy_hour_id: Optional[str]
    happy_hour_label: Optional[str]
    outing_date: str
    start_time: str
    end_time: str
    members: list[MemberOut]


class SuggestionOut(BaseModel):
    day_of_week: str
    start_time: str
    end_time: str
    venue_count: int
    next_date: str
    venue_ids: list[str]


# ---------- Helpers ----------

def _parse_hhmm(s: str) -> dt_time:
    try:
        h, m = s.split(":")
        return dt_time(int(h), int(m))
    except ValueError:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=f"Invalid time format: {s!r}. Expected HH:MM.",
        )


def _outing_to_out(outing: Outing, db: Session) -> OutingOut:
    venue = db.query(Venue).filter(Venue.id == outing.venue_id).one_or_none()
    hh_label = None
    if outing.happy_hour_id:
        hh = db.query(HappyHour).filter(HappyHour.id == outing.happy_hour_id).one_or_none()
        hh_label = hh.label if hh else None

    members_out = []
    for m in outing.members:
        user = db.query(User).filter(User.id == m.user_id).one_or_none()
        members_out.append(MemberOut(
            user_id=str(m.user_id),
            display_name=user.display_name if user else None,
            email=user.email if user else None,
            phone=user.phone if user else None,
            status=m.status,
            invited_at=m.invited_at.isoformat(),
            responded_at=m.responded_at.isoformat() if m.responded_at else None,
        ))

    return OutingOut(
        id=str(outing.id),
        organizer_id=str(outing.organizer_id),
        venue_id=str(outing.venue_id),
        venue_name=venue.name if venue else "(unknown)",
        venue_address=venue.address if venue else None,
        happy_hour_id=str(outing.happy_hour_id) if outing.happy_hour_id else None,
        happy_hour_label=hh_label,
        outing_date=outing.outing_date.isoformat(),
        start_time=outing.start_time.strftime("%H:%M"),
        end_time=outing.end_time.strftime("%H:%M"),
        members=members_out,
    )


# ---------- Routes ----------

@router.post("", response_model=OutingOut, status_code=status.HTTP_201_CREATED)
def post_outing(
    payload: CreateOutingRequest,
    user: User = Depends(get_current_verified_user),
    db: Session = Depends(get_db),
):
    try:
        outing = create_outing(
            db,
            organizer=user,
            venue_id=payload.venue_id,
            happy_hour_id=payload.happy_hour_id,
            outing_date=payload.outing_date,
            start_time=_parse_hhmm(payload.start_time),
            end_time=_parse_hhmm(payload.end_time),
        )
        db.commit()
    except ValueError as e:
        db.rollback()
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail=str(e))

    refreshed = get_outing_with_members(db, outing.id)
    return _outing_to_out(refreshed, db)


@router.get("", response_model=list[OutingOut])
def get_my_outings(
    user: User = Depends(get_current_verified_user),
    db: Session = Depends(get_db),
):
    outings = list_outings_for_user(db, user)
    # Eager-load members for each
    refreshed = [get_outing_with_members(db, o.id) for o in outings]
    return [_outing_to_out(o, db) for o in refreshed if o]


@router.get("/suggest", response_model=Optional[SuggestionOut])
def get_suggestion(
    lat: float = Query(...),
    lng: float = Query(...),
    radius_miles: float = Query(10.0, ge=0.5, le=50.0),
    window_hours: int = Query(3, ge=1, le=6),
    user: User = Depends(get_current_verified_user),
    db: Session = Depends(get_db),
):
    result = suggest_outing(
        db,
        lat=lat,
        lng=lng,
        radius_miles=radius_miles,
        window_hours=window_hours,
    )
    if not result:
        return None
    return SuggestionOut(**result)


@router.get("/{outing_id}", response_model=OutingOut)
def get_outing(
    outing_id: uuid.UUID,
    user: User = Depends(get_current_verified_user),
    db: Session = Depends(get_db),
):
    outing = get_outing_with_members(db, outing_id)
    if not outing:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Outing not found")
    # Authorize: only organizer or members can view
    is_member = any(m.user_id == user.id for m in outing.members)
    if outing.organizer_id != user.id and not is_member:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="You don't have access to this outing.",
        )
    return _outing_to_out(outing, db)


@router.post("/{outing_id}/invite", response_model=OutingOut)
def post_invite(
    outing_id: uuid.UUID,
    payload: InviteRequest,
    background: BackgroundTasks,
    user: User = Depends(get_current_verified_user),
    db: Session = Depends(get_db),
):
    outing = get_outing_with_members(db, outing_id)
    if not outing:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Outing not found")

    contacts_dicts = (
        [{"phone": c.phone, "email": c.email} for c in payload.contacts]
        if payload.contacts else None
    )

    try:
        result = invite_to_outing(
            db,
            organizer=user,
            outing=outing,
            user_ids=payload.user_ids,
            contacts=contacts_dicts,
        )
        db.commit()
    except PermissionError as e:
        db.rollback()
        raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail=str(e))
    except ValueError as e:
        db.rollback()
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail=str(e))

    # Fire push notifications in the background
    from app.notifications.sender import send_invitation_pushes_background
    background.add_task(
        send_invitation_pushes_background,
        outing_id=str(outing.id),
        invited_user_ids=[str(u) for u in result["invited_user_ids"]],
        organizer_display=user.display_name or user.email,
    )

    # Expire cached relationships so the re-query loads fresh members
    db.expire_all()
    refreshed = get_outing_with_members(db, outing.id)
    return _outing_to_out(refreshed, db)


@router.post("/{outing_id}/respond", response_model=OutingOut)
def post_respond(
    outing_id: uuid.UUID,
    payload: RespondRequest,
    background: BackgroundTasks,
    user: User = Depends(get_current_verified_user),
    db: Session = Depends(get_db),
):
    outing = get_outing_with_members(db, outing_id)
    if not outing:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Outing not found")

    try:
        respond_to_outing(db, user=user, outing=outing, response=payload.response)
        db.commit()
    except PermissionError as e:
        db.rollback()
        raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail=str(e))
    except ValueError as e:
        db.rollback()
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail=str(e))

    # Notify organizer of the response
    from app.notifications.sender import send_response_push_background
    background.add_task(
        send_response_push_background,
        outing_id=str(outing.id),
        organizer_user_id=str(outing.organizer_id),
        responder_display=user.display_name or user.email,
        response=payload.response,
    )

    # Expire cached relationships for fresh re-load
    db.expire_all()
    refreshed = get_outing_with_members(db, outing.id)
    return _outing_to_out(refreshed, db)
