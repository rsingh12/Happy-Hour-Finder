"""
Venues endpoints:
  GET /venues          — search venues nearby a location, with optional date/time filter
  GET /venues/{id}     — single venue with all its happy hours
"""

from __future__ import annotations

import uuid
from datetime import date as dt_date, time as dt_time
from typing import Optional

from fastapi import APIRouter, Depends, HTTPException, Query, status
from pydantic import BaseModel, Field
from sqlalchemy.orm import Session

from app.auth.deps import get_current_user
from app.database import get_db
from app.models import User
from app.venues.service import find_venues_nearby, get_venue_with_happy_hours


router = APIRouter(prefix="/venues", tags=["venues"])


# ---------- Schemas ----------

class HappyHourOut(BaseModel):
    id: str
    label: str
    days: list[str]
    start_time: Optional[str]
    end_time: Optional[str]
    specials: list[str]
    source: Optional[str]
    confidence: Optional[str]


class VenueOut(BaseModel):
    id: str
    name: str
    address: Optional[str]
    latitude: Optional[float]
    longitude: Optional[float]
    google_maps_url: Optional[str]
    website: Optional[str]
    phone: Optional[str]
    rating: Optional[float]


class VenueWithDistance(VenueOut):
    distance_miles: float
    happy_hours: list[HappyHourOut]


class VenueDetail(VenueOut):
    happy_hours: list[HappyHourOut]


# ---------- Helpers ----------

DAY_NAMES = {
    0: "Monday", 1: "Tuesday", 2: "Wednesday", 3: "Thursday",
    4: "Friday", 5: "Saturday", 6: "Sunday",
}


def _hh_to_out(hh) -> HappyHourOut:
    return HappyHourOut(
        id=str(hh.id),
        label=hh.label,
        days=hh.days or [],
        start_time=hh.start_time.strftime("%H:%M") if hh.start_time else None,
        end_time=hh.end_time.strftime("%H:%M") if hh.end_time else None,
        specials=hh.specials or [],
        source=hh.source,
        confidence=hh.confidence,
    )


def _venue_to_out(v) -> VenueOut:
    return VenueOut(
        id=str(v.id),
        name=v.name,
        address=v.address,
        latitude=v.latitude,
        longitude=v.longitude,
        google_maps_url=v.google_maps_url,
        website=v.website,
        phone=v.phone,
        rating=v.rating,
    )


def _parse_hhmm(s: Optional[str]) -> Optional[dt_time]:
    if not s:
        return None
    try:
        hh, mm = s.split(":")
        return dt_time(int(hh), int(mm))
    except ValueError:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=f"Invalid time format: {s!r}. Expected HH:MM.",
        )


# ---------- Routes ----------

@router.get("", response_model=list[VenueWithDistance])
def list_venues_nearby(
    lat: float = Query(..., description="Latitude of the user's location."),
    lng: float = Query(..., description="Longitude of the user's location."),
    radius_miles: float = Query(10.0, ge=0.5, le=50.0),
    date: Optional[dt_date] = Query(
        None,
        description="If set, only return venues with a happy hour on this date's day-of-week.",
    ),
    day: Optional[str] = Query(
        None,
        description="Override day-of-week filter, e.g. 'Friday'. Takes precedence over `date`.",
    ),
    start_time: Optional[str] = Query(None, description="HH:MM lower bound for the happy hour window."),
    end_time: Optional[str] = Query(None, description="HH:MM upper bound for the happy hour window."),
    limit: int = Query(50, ge=1, le=200),
    user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    """Find venues within `radius_miles` of the given coordinates."""
    effective_day = day
    if not effective_day and date is not None:
        effective_day = DAY_NAMES.get(date.weekday())

    start_t = _parse_hhmm(start_time)
    end_t = _parse_hhmm(end_time)
    if (start_t and not end_t) or (end_t and not start_t):
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Provide both start_time and end_time, or neither.",
        )

    matches = find_venues_nearby(
        db,
        lat=lat,
        lng=lng,
        radius_miles=radius_miles,
        day=effective_day,
        start_time=start_t,
        end_time=end_t,
        limit=limit,
    )

    return [
        VenueWithDistance(
            **_venue_to_out(m["venue"]).model_dump(),
            distance_miles=m["distance_miles"],
            happy_hours=[_hh_to_out(hh) for hh in m["happy_hours"]],
        )
        for m in matches
    ]


@router.get("/{venue_id}", response_model=VenueDetail)
def get_venue(
    venue_id: uuid.UUID,
    user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    venue = get_venue_with_happy_hours(db, venue_id)
    if venue is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Venue not found")
    return VenueDetail(
        **_venue_to_out(venue).model_dump(),
        happy_hours=[_hh_to_out(hh) for hh in venue.happy_hours],
    )
