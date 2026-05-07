"""
Venue search service: bounding-box geo lookup + happy-hour day/time
filtering. Returns venues sorted by Haversine distance to the user.
"""

from __future__ import annotations

from datetime import time as dt_time
from typing import Optional

from sqlalchemy import and_, or_
from sqlalchemy.orm import Session, joinedload

from app.models import HappyHour, Venue
from app.scanner.geo import bounding_box, haversine_miles


def find_venues_nearby(
    db: Session,
    *,
    lat: float,
    lng: float,
    radius_miles: float = 10.0,
    day: Optional[str] = None,
    start_time: Optional[dt_time] = None,
    end_time: Optional[dt_time] = None,
    limit: int = 50,
) -> list[dict]:
    """
    Return venues within `radius_miles` of (lat, lng), optionally filtered
    to those running a happy hour on `day` that overlaps the time window
    [start_time, end_time].

    Returns a list of dicts with the venue plus its matching happy hours
    and computed distance, sorted nearest-first.
    """
    min_lat, max_lat, min_lng, max_lng = bounding_box(lat, lng, radius_miles)

    q = (
        db.query(Venue)
        .options(joinedload(Venue.happy_hours))
        .filter(Venue.latitude.isnot(None), Venue.longitude.isnot(None))
        .filter(
            Venue.latitude.between(min_lat, max_lat),
            Venue.longitude.between(min_lng, max_lng),
        )
    )

    venues = q.all()

    # Filter post-hoc by exact distance + happy hour day/time
    results: list[dict] = []
    for v in venues:
        distance = haversine_miles(lat, lng, v.latitude, v.longitude)
        if distance > radius_miles:
            continue

        matching_hh = list(v.happy_hours)
        if day:
            matching_hh = [hh for hh in matching_hh if day in (hh.days or [])]
        if start_time and end_time:
            # Overlap test: hh.start < query_end AND hh.end > query_start
            matching_hh = [
                hh for hh in matching_hh
                if hh.start_time and hh.end_time
                and hh.start_time < end_time
                and hh.end_time > start_time
            ]

        # If filters were specified and nothing matched, skip the venue
        if (day or (start_time and end_time)) and not matching_hh:
            continue

        results.append({
            "venue": v,
            "happy_hours": matching_hh,
            "distance_miles": round(distance, 2),
        })

    results.sort(key=lambda r: r["distance_miles"])
    return results[:limit]


def get_venue_with_happy_hours(db: Session, venue_id) -> Optional[Venue]:
    """Single venue with all its happy hours pre-loaded."""
    return (
        db.query(Venue)
        .options(joinedload(Venue.happy_hours))
        .filter(Venue.id == venue_id)
        .one_or_none()
    )
