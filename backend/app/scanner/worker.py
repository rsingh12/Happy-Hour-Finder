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

from sqlalchemy.orm import Session

from app.database import SessionLocal
from app.models import HappyHour, Venue
from app.scanner.happy_hour_parser import parse_all_places
from app.scanner.scanner import scan_google_maps
from app.scanner.website_finder import find_websites_for_venues
from app.scanner.yelp_discovery import discover_venues as yelp_discover
from app.scanner.yelp_discovery import is_available as yelp_available


# ---------- Helpers ----------

def _parse_time_str(s: Optional[str]) -> Optional[dt_time]:
    if not s:
        return None
    try:
        hh, mm = s.split(":")
        return dt_time(hour=int(hh), minute=int(mm))
    except (ValueError, AttributeError):
        return None


def _find_or_create_venue(db: Session, place: dict) -> Venue:
    """Upsert a venue by name + address."""
    name = place.get("name") or "Unknown"
    address = place.get("address") or ""

    venue = (
        db.query(Venue)
        .filter(Venue.name == name, Venue.address == address)
        .one_or_none()
    )

    if venue is None:
        venue = Venue(
            name=name,
            address=address,
            latitude=place.get("latitude"),
            longitude=place.get("longitude"),
            google_maps_url=place.get("maps_url") or None,
            website=place.get("website") or None,
            phone=place.get("phone") or None,
            rating=place.get("rating"),
        )
        db.add(venue)
        db.flush()
        print(f"  [+] Created venue: {name}")
    else:
        venue.latitude = place.get("latitude") or venue.latitude
        venue.longitude = place.get("longitude") or venue.longitude
        venue.google_maps_url = place.get("maps_url") or venue.google_maps_url
        venue.website = place.get("website") or venue.website
        venue.phone = place.get("phone") or venue.phone
        venue.rating = place.get("rating") or venue.rating
        print(f"  [~] Updated venue: {name}")

    return venue


def _replace_happy_hours(db: Session, venue: Venue, happy_hours: list[dict]) -> int:
    db.query(HappyHour).filter(HappyHour.venue_id == venue.id).delete()

    inserted = 0
    for hh in happy_hours:
        days = hh.get("days") or []
        if not isinstance(days, list):
            days = []

        record = HappyHour(
            venue_id=venue.id,
            label=hh.get("label") or "Happy Hour",
            days=days,
            start_time=_parse_time_str(hh.get("start_time")),
            end_time=_parse_time_str(hh.get("end_time")),
            specials=hh.get("specials") or [],
            source=hh.get("source"),
            confidence=hh.get("confidence"),
            scanned_at=datetime.now(timezone.utc),
        )
        db.add(record)
        inserted += 1

    return inserted


def _dedupe_places(places: list[dict]) -> list[dict]:
    seen = set()
    unique = []
    for p in places:
        key = (p.get("name", "").strip().lower(), p.get("address", "").strip().lower())
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
            venue = _find_or_create_venue(db, place)
            inserted = _replace_happy_hours(db, venue, place.get("happy_hours", []))
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
