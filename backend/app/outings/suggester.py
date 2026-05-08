"""
Density-based date/time suggester.

For a given (lat, lng, radius), returns the best (day, hour-window) over
the next 7 days based on how many nearby venues are running a happy hour
during each (day, hour) bucket.

Pure SQL aggregation — no ML, no per-user data. Works on day 1.
"""

from __future__ import annotations

from collections import defaultdict
from datetime import date as dt_date, datetime, timedelta
from typing import Optional

from sqlalchemy.orm import Session, joinedload

from app.models import HappyHour, Venue
from app.scanner.geo import bounding_box, haversine_miles


DAYS = ["Monday", "Tuesday", "Wednesday", "Thursday", "Friday", "Saturday", "Sunday"]


def suggest_outing(
    db: Session,
    *,
    lat: float,
    lng: float,
    radius_miles: float = 10.0,
    window_hours: int = 3,
    after_work_pref: bool = True,
) -> Optional[dict]:
    """
    Find the (day, start_hour, end_hour) in the next 7 days with the most
    venues running happy hour during that window.

    Returns:
      {
        "day_of_week": "Thursday",
        "start_time":  "16:00",
        "end_time":    "19:00",
        "venue_count": 8,
        "next_date":   "2026-05-08",
        "venue_ids":   [<uuid>, ...]
      }
    Or None if there are no happy hours nearby.
    """
    # 1. Pull nearby happy hours via bounding box, then filter exact distance
    min_lat, max_lat, min_lng, max_lng = bounding_box(lat, lng, radius_miles)

    candidates = (
        db.query(HappyHour)
        .options(joinedload(HappyHour.venue))
        .filter(
            HappyHour.start_time.isnot(None),
            HappyHour.end_time.isnot(None),
            HappyHour.days != [],
        )
        .join(Venue, Venue.id == HappyHour.venue_id)
        .filter(
            Venue.latitude.isnot(None),
            Venue.longitude.isnot(None),
            Venue.latitude.between(min_lat, max_lat),
            Venue.longitude.between(min_lng, max_lng),
        )
        .all()
    )

    nearby: list[HappyHour] = []
    for hh in candidates:
        v = hh.venue
        if haversine_miles(lat, lng, v.latitude, v.longitude) <= radius_miles:
            nearby.append(hh)

    if not nearby:
        return None

    # 2. Bucket counts: (day, hour) -> set of venue_ids
    bucket_to_venues: dict[tuple[str, int], set] = defaultdict(set)
    for hh in nearby:
        days = hh.days or []
        start_h = hh.start_time.hour
        end_h = hh.end_time.hour
        # Handle midnight wrap: 22:00 -> 01:00 means hours 22, 23, 0
        if end_h <= start_h:
            hours = list(range(start_h, 24)) + list(range(0, end_h))
        else:
            hours = list(range(start_h, end_h))
        for d in days:
            for h in hours:
                bucket_to_venues[(d, h)].add(hh.venue_id)

    # 3. For each (day, start_hour), count distinct venues across the
    #    `window_hours` hour window. Best (highest count) wins.
    best = None
    for d in DAYS:
        for start in range(0, 24):
            window_venues: set = set()
            for h in range(start, start + window_hours):
                key = (d, h % 24)
                window_venues |= bucket_to_venues.get(key, set())
            count = len(window_venues)
            if count == 0:
                continue
            score = count
            # Bias toward 16:00-19:00 if requested (typical "after work")
            if after_work_pref and 15 <= start <= 17:
                score += 0.5
            if best is None or score > best["score"]:
                best = {
                    "score": score,
                    "day": d,
                    "start": start,
                    "end": (start + window_hours) % 24,
                    "venues": window_venues,
                }

    if best is None:
        return None

    # 4. Compute the next concrete calendar date for that day-of-week
    today = dt_date.today()
    target_idx = DAYS.index(best["day"])
    days_ahead = (target_idx - today.weekday()) % 7
    if days_ahead == 0:
        # Today is the suggested day — keep it (user can plan for tonight)
        next_date = today
    else:
        next_date = today + timedelta(days=days_ahead)

    return {
        "day_of_week": best["day"],
        "start_time": f"{best['start']:02d}:00",
        "end_time": f"{best['end']:02d}:00",
        "venue_count": len(best["venues"]),
        "next_date": next_date.isoformat(),
        "venue_ids": [str(v) for v in best["venues"]],
    }
