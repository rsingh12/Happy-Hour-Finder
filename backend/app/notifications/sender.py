"""
Push notification senders for outing events.

These run as FastAPI background tasks: the route returns a response
immediately, then the push fires shortly after. If a push fails, the
core action (the invite or response) is unaffected.
"""

from __future__ import annotations

import uuid
from typing import Optional

from sqlalchemy.orm import Session

from app.database import SessionLocal
from app.models import DeviceToken, Outing, Venue
from app.notifications.apns import send_push


def _device_tokens_for_user(db: Session, user_id: uuid.UUID) -> list[str]:
    return [
        t.apns_token
        for t in db.query(DeviceToken).filter(DeviceToken.user_id == user_id).all()
    ]


def send_invitation_pushes_background(
    *,
    outing_id: str,
    invited_user_ids: list[str],
    organizer_display: str,
) -> None:
    """Background task: notify each invited user about a new outing invite."""
    db: Session = SessionLocal()
    try:
        outing = db.query(Outing).filter(Outing.id == uuid.UUID(outing_id)).one_or_none()
        if not outing:
            return
        venue = db.query(Venue).filter(Venue.id == outing.venue_id).one_or_none()
        venue_name = venue.name if venue else "an outing"

        title = f"🍻 {organizer_display} invited you"
        body = (
            f"{venue_name} on {outing.outing_date.strftime('%a %b %d')}, "
            f"{outing.start_time.strftime('%H:%M')}–{outing.end_time.strftime('%H:%M')}"
        )
        extras = {"outing_id": outing_id, "type": "invitation"}

        for uid in invited_user_ids:
            try:
                tokens = _device_tokens_for_user(db, uuid.UUID(uid))
                for tok in tokens:
                    send_push(apns_token=tok, title=title, body=body, payload_extras=extras)
            except Exception as e:
                print(f"[push] Failed to send invite to {uid}: {e}")
    finally:
        db.close()


def send_response_push_background(
    *,
    outing_id: str,
    organizer_user_id: str,
    responder_display: str,
    response: str,
) -> None:
    """Background task: notify the organizer when someone responds to an invite."""
    db: Session = SessionLocal()
    try:
        outing = db.query(Outing).filter(Outing.id == uuid.UUID(outing_id)).one_or_none()
        if not outing:
            return
        venue = db.query(Venue).filter(Venue.id == outing.venue_id).one_or_none()
        venue_name = venue.name if venue else "your outing"

        emoji = "✅" if response == "accepted" else "❌"
        title = f"{emoji} {responder_display} {response}"
        body = f"They {response} your invite to {venue_name}."
        extras = {"outing_id": outing_id, "type": "response", "response": response}

        try:
            tokens = _device_tokens_for_user(db, uuid.UUID(organizer_user_id))
            for tok in tokens:
                send_push(apns_token=tok, title=title, body=body, payload_extras=extras)
        except Exception as e:
            print(f"[push] Failed to send response notif to {organizer_user_id}: {e}")
    finally:
        db.close()
