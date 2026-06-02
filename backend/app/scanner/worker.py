"""
Scanner worker — discovers venues, extracts happy hour info, and upserts
results into Postgres.

Discovery strategy (in order of preference):
  1. Yelp Fusion API     — preferred when YELP_API_KEY is set. Free,
                           comprehensive, category-based.
  2. Google Maps scrape  — fallback when Yelp is unavailable or returns
                           nothing for the requested area.

After discovery, every venue's website is crawled and its happy hour
info is extracted via Claude Haiku (text LLM + vision fallback).

Usage examples:

    # Yelp-based scan around a lat/lng (recommended)
    python -m app.scanner.worker --lat 38.7521 --lng -121.296 --radius 10

    # With a max venue count
    python -m app.scanner.worker --lat 38.7521 --lng -121.296 --max 60

    # Force the legacy Selenium-search-based path (no Yelp)
    python -m app.scanner.worker --query "happy hour near 95747" --legacy

The worker is idempotent: re-running upserts existing venues by
(name, address) and replaces their happy hours with the latest scrape.
"""

from __future__ import annotations

import argparse
import sys
from datetime import datetime, time as dt_time, timezone
from typing import Optional

from rapidfuzz import fuzz
from sqlalchemy.orm import Session

from app.database import SessionLocal
from app.models import Confidence, ExtractionSource, HappyHour, Venue
from app.scanner.geo import haversine_meters
from app.scanner.happy_hour_parser import parse_all_places
from app.scanner.normalize import (
    extract_google_place_id,
    extract_street_number,
    extract_zip,
    normalize_address,
    normalize_name,
)
from app.scanner.scanner import scan_google_maps
from app.scanner.website_finder import find_websites_for_venues
from app.scanner.yelp_discovery import discover_venues as yelp_discover
from app.scanner.yelp_discovery import is_available as yelp_available


# False positives (merging distinct venues) are worse than false negatives
# (creating a near-duplicate), so thresholds are deliberately conservative.
GEO_RADIUS_METERS = 75.0
FUZZY_NAME_THRESHOLD = 92
FUZZY_GEO_RADIUS_METERS = 500.0
DEFAULT_HH_LABEL = "Happy Hour"


def _coerce_enum(value, enum_cls):
    """Coerce a raw string into the given enum, or return None on miss.

    Fails fast on typos — a bad string raises ValueError here rather than
    silently writing garbage that the DB will reject on commit.
    """
    if value is None or value == "":
        return None
    if isinstance(value, enum_cls):
        return value
    return enum_cls(value)


def _parse_time_str(s: Optional[str]) -> Optional[dt_time]:
    if not s:
        return None
    try:
        hh, mm = s.split(":")
        return dt_time(hour=int(hh), minute=int(mm))
    except (ValueError, AttributeError):
        return None


def _match_venue(
    db: Session, place: dict, *, place_id: Optional[str], norm_name: str
) -> Optional[Venue]:
    """Find an existing venue matching `place` via a 4-step cascade.

    1. Exact `google_place_id` (durable, near-zero false-positive rate)
    2. Same normalized_name within ~75m geo radius
    3. Same normalized_name + same street number + same ZIP
    4. Fuzzy name match within a wider geo radius (~500m)
    """
    lat = place.get("latitude")
    lng = place.get("longitude")
    address = place.get("address") or ""
    street_num = extract_street_number(address)
    zip_code = extract_zip(address)

    if place_id:
        match = (
            db.query(Venue)
            .filter(Venue.google_place_id == place_id)
            .one_or_none()
        )
        if match:
            return match

    if not norm_name:
        return None

    candidates = (
        db.query(Venue).filter(Venue.normalized_name == norm_name).all()
    )

    if lat is not None and lng is not None:
        for c in candidates:
            d = haversine_meters(lat, lng, c.latitude, c.longitude)
            if d is not None and d <= GEO_RADIUS_METERS:
                return c

    if street_num and zip_code:
        for c in candidates:
            if (
                extract_street_number(c.address) == street_num
                and extract_zip(c.address) == zip_code
            ):
                return c

    if lat is not None and lng is not None:
        # 500m ≈ 0.005° latitude; box is loose because actual distance is
        # checked below. Prefix filter on normalized_name keeps the candidate
        # set small in dense areas (Manhattan can return hundreds per box).
        deg = 0.01
        prefix = norm_name[:4]
        nearby = (
            db.query(Venue)
            .filter(
                Venue.latitude.between(lat - deg, lat + deg),
                Venue.longitude.between(lng - deg, lng + deg),
                Venue.normalized_name.ilike(f"{prefix}%"),
            )
            .all()
        )
        for c in nearby:
            d = haversine_meters(lat, lng, c.latitude, c.longitude)
            if d is None or d > FUZZY_GEO_RADIUS_METERS:
                continue
            if fuzz.token_set_ratio(norm_name, c.normalized_name) >= FUZZY_NAME_THRESHOLD:
                return c

    return None


def _find_or_create_venue(db: Session, place: dict) -> tuple[Venue, bool]:
    """Match an existing venue with the cascade, or create a new one.

    Returns `(venue, is_new)`. On match, `normalized_name` is always
    recomputed from the live name (so a corrected venue name updates its
    lookup key); other fields fill in only when previously null/empty.
    """
    name = place.get("name") or "Unknown"
    place_id = extract_google_place_id(place.get("maps_url"))
    norm_name = normalize_name(name)

    venue = _match_venue(db, place, place_id=place_id, norm_name=norm_name)

    if venue is None:
        venue = Venue(
            name=name,
            normalized_name=norm_name,
            address=place.get("address") or "",
            latitude=place.get("latitude"),
            longitude=place.get("longitude"),
            google_maps_url=place.get("maps_url") or None,
            google_place_id=place_id,
            website=place.get("website") or None,
            phone=place.get("phone") or None,
            rating=place.get("rating"),
        )
        db.add(venue)
        db.flush()
        print(f"  [+] Created venue: {name}")
        return venue, True

    venue.normalized_name = normalize_name(venue.name)
    venue.google_place_id = venue.google_place_id or place_id
    venue.latitude = venue.latitude if venue.latitude is not None else place.get("latitude")
    venue.longitude = venue.longitude if venue.longitude is not None else place.get("longitude")
    venue.google_maps_url = venue.google_maps_url or place.get("maps_url")
    venue.website = venue.website or place.get("website")
    venue.phone = venue.phone or place.get("phone")
    venue.rating = venue.rating if venue.rating is not None else place.get("rating")
    print(f"  [~] Matched existing venue: {venue.name}")
    return venue, False


def _hh_key(days, start_time, end_time, label: str) -> tuple:
    """Days are treated as a set (Mon-Fri == Mon,Tue,Wed,Thu,Fri)."""
    return (
        frozenset(d for d in (days or []) if d),
        start_time,
        end_time,
        (label or "").strip(),
    )


def _merge_happy_hours(
    db: Session, venue: Venue, happy_hours: list[dict], *, is_new_venue: bool = False
) -> int:
    """Merge scanned happy hours into the venue's existing set.

    Non-destructive: unmatched-existing entries are preserved. Matching
    entries (same key) refresh `scanned_at`, `source`, `confidence`, and
    `specials` when the new scrape provides them.
    """
    if not happy_hours:
        return 0

    existing_by_key: dict[tuple, HappyHour] = {}
    if not is_new_venue:
        existing = db.query(HappyHour).filter(HappyHour.venue_id == venue.id).all()
        existing_by_key = {
            _hh_key(h.days, h.start_time, h.end_time, h.label): h for h in existing
        }

    now = datetime.now(timezone.utc)
    upserted = 0
    for hh in happy_hours:
        days = hh.get("days") or []
        if not isinstance(days, list):
            days = []
        start_t = _parse_time_str(hh.get("start_time"))
        end_t = _parse_time_str(hh.get("end_time"))
        label = hh.get("label") or DEFAULT_HH_LABEL
        source = _coerce_enum(hh.get("source"), ExtractionSource)
        confidence = _coerce_enum(hh.get("confidence"), Confidence)
        key = _hh_key(days, start_t, end_t, label)

        match = existing_by_key.get(key)
        if match is not None:
            match.scanned_at = now
            match.source = source or match.source
            match.confidence = confidence or match.confidence
            # Don't wipe a previously-extracted list if the new scrape lacks one.
            if hh.get("specials"):
                match.specials = hh["specials"]
            upserted += 1
            continue

        db.add(HappyHour(
            venue_id=venue.id,
            label=label,
            days=days,
            start_time=start_t,
            end_time=end_t,
            specials=hh.get("specials") or [],
            source=source,
            confidence=confidence,
            scanned_at=now,
        ))
        upserted += 1

    return upserted


def _dedupe_places(places: list[dict]) -> list[dict]:
    """In-batch dedupe using the same normalization rules as the DB-side
    cascade match, so what `_match_venue` would collapse is also collapsed
    upstream."""
    seen = set()
    unique = []
    for p in places:
        key = (normalize_name(p.get("name")), normalize_address(p.get("address")))
        if key in seen:
            continue
        seen.add(key)
        unique.append(p)
    return unique


# ---------- Discovery paths ----------

def _discover_via_yelp(
    lat: float, lng: float, radius_miles: float, max_results: int, headless: bool
) -> list[dict]:
    """Yelp-based discovery + Selenium-based website lookup."""
    venues = yelp_discover(
        lat=lat, lng=lng, radius_miles=radius_miles, max_results=max_results
    )
    if not venues:
        return []

    # Yelp doesn't reliably return business websites; look them up.
    find_websites_for_venues(venues, headless=headless)

    # Drop venues we couldn't find a website for — without a website, we
    # have nothing to crawl for happy hour info.
    with_websites = [v for v in venues if v.get("website")]
    print(
        f"[discovery] {len(with_websites)}/{len(venues)} venues have websites "
        f"to crawl. {len(venues) - len(with_websites)} skipped."
    )
    return with_websites


def _discover_via_legacy(
    queries: list[str], max_per_query: int, headless: bool
) -> list[dict]:
    """Legacy Google Maps scraping path (now a fallback)."""
    all_places: list[dict] = []
    for q in queries:
        print(f"\n[legacy] Query: {q!r}")
        places = scan_google_maps(query=q, max_results=max_per_query, headless=headless)
        all_places.extend(places)
    return _dedupe_places(all_places)


DEFAULT_LEGACY_QUERIES = [
    "happy hour bars and restaurants near 95747",
    "social hour bars and restaurants near 95747",
    "late night specials bars near 95747",
    "tapas restaurants near 95747",
    "breweries near 95747",
    "wine bars near 95747",
    "gastropubs near 95747",
    "sports bars near 95747",
]


# ---------- Main entry ----------

def run_scan(
    *,
    lat: Optional[float] = None,
    lng: Optional[float] = None,
    radius_miles: float = 10.0,
    max_results: int = 60,
    legacy_queries: Optional[list[str]] = None,
    force_legacy: bool = False,
    headless: bool = True,
) -> dict:
    """
    Run a full scan: discovery -> website crawl -> LLM extraction -> DB upsert.

    Pass either:
      - lat + lng + radius_miles (preferred, uses Yelp)
      - legacy_queries (forces the old Selenium-based path)

    If lat/lng are provided AND Yelp is available AND not force_legacy,
    Yelp is used. Otherwise the Selenium scraper runs with the given
    legacy_queries (or DEFAULT_LEGACY_QUERIES).
    """
    use_yelp = (
        lat is not None
        and lng is not None
        and yelp_available()
        and not force_legacy
    )

    if use_yelp:
        print(
            f"[worker] Discovery: Yelp Fusion ({lat}, {lng}) within {radius_miles} miles, max {max_results}"
        )
        places = _discover_via_yelp(lat, lng, radius_miles, max_results, headless)
    else:
        if force_legacy:
            reason = "user requested --legacy"
        elif lat is None or lng is None:
            reason = "no lat/lng provided"
        else:
            reason = "YELP_API_KEY not set"
        print(f"[worker] Discovery: Selenium fallback ({reason})")
        queries = legacy_queries or DEFAULT_LEGACY_QUERIES
        places = _discover_via_legacy(queries, max_per_query=20, headless=headless)

    if not places:
        print("[worker] No places discovered. Aborting.")
        return {"venues": 0, "happy_hours": 0, "skipped": 0}

    print(f"\n[worker] Parsing happy hours from {len(places)} websites...")
    enriched = parse_all_places(places)

    db: Session = SessionLocal()
    venues_count = 0
    happy_hours_count = 0
    skipped = len(places) - len(enriched)

    try:
        for place in enriched:
            venue, is_new = _find_or_create_venue(db, place)
            inserted = _merge_happy_hours(
                db, venue, place.get("happy_hours", []), is_new_venue=is_new
            )
            venues_count += 1
            happy_hours_count += inserted
            db.commit()
    except Exception as e:
        db.rollback()
        print(f"[worker] DB error: {e}")
        raise
    finally:
        db.close()

    print(
        f"[worker] Done. Venues touched: {venues_count}, "
        f"happy hours saved: {happy_hours_count}, skipped: {skipped}"
    )
    return {
        "venues": venues_count,
        "happy_hours": happy_hours_count,
        "skipped": skipped,
    }


def main() -> int:
    parser = argparse.ArgumentParser(description="Run the Happy Hour scanner.")
    parser.add_argument("--lat", type=float, help="Latitude (preferred path: Yelp).")
    parser.add_argument("--lng", type=float, help="Longitude (preferred path: Yelp).")
    parser.add_argument(
        "--radius",
        type=float,
        default=10.0,
        help="Search radius in miles (default 10, Yelp max ~25).",
    )
    parser.add_argument(
        "--max",
        dest="max_results",
        type=int,
        default=60,
        help="Maximum number of venues to process (default 60).",
    )
    parser.add_argument(
        "--legacy",
        action="store_true",
        help="Force the legacy Selenium-based Google Maps scraper.",
    )
    parser.add_argument(
        "--query",
        action="append",
        help="Legacy: one or more Google Maps search queries (used with --legacy).",
    )
    parser.add_argument(
        "--show-browser",
        action="store_true",
        help="Run with a visible Chrome window (default: headless).",
    )
    args = parser.parse_args()

    if not args.legacy and (args.lat is None or args.lng is None):
        # Default: use Roseville 95747 if no coords given. This makes the
        # one-shot `python -m app.scanner.worker` keep working.
        print("[worker] No --lat/--lng given; defaulting to 95747 (Roseville, CA).")
        args.lat, args.lng = 38.7521, -121.2966

    summary = run_scan(
        lat=args.lat,
        lng=args.lng,
        radius_miles=args.radius,
        max_results=args.max_results,
        legacy_queries=args.query,
        force_legacy=args.legacy,
        headless=not args.show_browser,
    )
    print(f"\nSummary: {summary}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
