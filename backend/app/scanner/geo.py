"""
Geo helpers — extract lat/lng from Google Maps URLs and compute
bounding boxes for radius queries.
"""

import math
import re
from typing import Optional


# Google Maps URLs encode TWO coordinate pairs:
#   1. The map viewport center, in `@LAT,LNG,ZOOMz`
#   2. The actual place location, in `!8m2!3dLAT!4dLNG` (inside the data= blob)
# We want #2; the viewport is just where the search was performed and is
# the same across all results from a single search.
_VENUE_LATLNG_PATTERN = re.compile(r"!8m2!3d(-?\d+\.\d+)!4d(-?\d+\.\d+)")
_VIEWPORT_LATLNG_PATTERN = re.compile(r"@(-?\d+\.\d+),(-?\d+\.\d+),")


EARTH_RADIUS_MILES = 3958.8
EARTH_RADIUS_METERS = 6_371_000.0


def parse_latlng_from_maps_url(url: str) -> Optional[tuple[float, float]]:
    """
    Extract a venue's true (lat, lng) from a Google Maps place URL.
    Prefers the !8m2!3dLAT!4dLNG (place location) over the @LAT,LNG (viewport).
    """
    if not url:
        return None

    # Try the actual venue coordinates first
    m = _VENUE_LATLNG_PATTERN.search(url)
    if not m:
        # Fall back to viewport center if the place segment isn't present
        m = _VIEWPORT_LATLNG_PATTERN.search(url)
    if not m:
        return None

    try:
        return (float(m.group(1)), float(m.group(2)))
    except ValueError:
        return None


def bounding_box(lat: float, lng: float, radius_miles: float) -> tuple[float, float, float, float]:
    """
    Return (min_lat, max_lat, min_lng, max_lng) covering a square of
    ~`radius_miles` half-edge around (lat, lng). Good enough for v1
    (slight over-fetch at high latitudes is fine — we filter by
    Haversine distance afterwards if we need exact ordering).
    """
    lat_delta = radius_miles / 69.0
    # 1 deg lng = 69 * cos(lat) miles
    cos_lat = math.cos(math.radians(lat))
    lng_delta = radius_miles / (69.0 * cos_lat) if cos_lat else 1.0
    return (lat - lat_delta, lat + lat_delta, lng - lng_delta, lng + lng_delta)


def _haversine_radians(lat1: float, lng1: float, lat2: float, lng2: float) -> float:
    lat1_r, lat2_r = math.radians(lat1), math.radians(lat2)
    dlat = lat2_r - lat1_r
    dlng = math.radians(lng2 - lng1)
    a = math.sin(dlat / 2) ** 2 + math.cos(lat1_r) * math.cos(lat2_r) * math.sin(dlng / 2) ** 2
    return 2 * math.asin(math.sqrt(a))


def haversine_miles(lat1: float, lng1: float, lat2: float, lng2: float) -> float:
    """Great-circle distance between two points in miles."""
    return EARTH_RADIUS_MILES * _haversine_radians(lat1, lng1, lat2, lng2)


def haversine_meters(
    lat1: Optional[float],
    lng1: Optional[float],
    lat2: Optional[float],
    lng2: Optional[float],
) -> Optional[float]:
    """Great-circle distance in meters; None if any coord is missing."""
    if None in (lat1, lng1, lat2, lng2):
        return None
    return EARTH_RADIUS_METERS * _haversine_radians(lat1, lng1, lat2, lng2)
