"""
Manual venue seeding — add a single venue to the database when Google
Maps search doesn't surface it.

Usage:
    # Preferred: a Google Maps place URL — auto-extracts name, address,
    # lat/lng, phone, website
    python -m app.scanner.add_venue --maps-url "https://www.google.com/maps/place/..."

    # Or: just a website URL — provide name + address yourself
    python -m app.scanner.add_venue \
        --website "https://www.monkscellar.com" \
        --name "Monks Cellar" \
        --address "240 Vernon St, Roseville, CA 95678"

After extracting venue details, the script crawls the website and runs
the same regex/LLM/vision happy-hour extraction the bulk scanner uses,
then upserts into Postgres.
"""

from __future__ import annotations

import argparse
import sys
from typing import Optional

from app.scanner.happy_hour_parser import parse_all_places
from app.scanner.scanner import scan_place_by_maps_url
from app.scanner.worker import _find_or_create_venue, _merge_happy_hours
from app.database import SessionLocal


def add_from_maps_url(maps_url: str, headless: bool = True) -> Optional[dict]:
    """Scrape Google Maps for a single venue, then run the parser + DB upsert."""
    print(f"[add-venue] Scraping Google Maps: {maps_url}")
    place = scan_place_by_maps_url(maps_url, headless=headless)
    if not place:
        print("[add-venue] Could not extract venue details from that URL.")
        return None
    print(f"[add-venue] Found: {place['name']} - {place.get('address')}")
    return _ingest(place)


def add_from_website(
    website: str,
    name: str,
    address: str,
    latitude: Optional[float] = None,
    longitude: Optional[float] = None,
    phone: Optional[str] = None,
) -> dict:
    """Skip Google Maps; user supplies the basics directly."""
    if not website.startswith("http"):
        website = "https://" + website
    place = {
        "name": name,
        "address": address,
        "website": website,
        "maps_url": None,
        "latitude": latitude,
        "longitude": longitude,
        "rating": None,
        "phone": phone or "",
    }
    print(f"[add-venue] Adding: {name} ({website})")
    return _ingest(place)


def _ingest(place: dict) -> dict:
    """Run the website crawl + happy hour extraction + DB upsert."""
    print("[add-venue] Crawling website for happy hour info...")
    enriched_list = parse_all_places([place])

    if not enriched_list:
        print(
            "[add-venue] No usable happy hour info found on the website. "
            "The venue is NOT being added — re-run with a different URL or "
            "manually insert via SQL if you want to keep it."
        )
        return {"venues": 0, "happy_hours": 0}

    enriched = enriched_list[0]

    db = SessionLocal()
    try:
        venue, is_new = _find_or_create_venue(db, enriched)
        inserted = _merge_happy_hours(
            db, venue, enriched.get("happy_hours", []), is_new_venue=is_new
        )
        db.commit()
    except Exception as e:
        db.rollback()
        print(f"[add-venue] DB error: {e}")
        raise
    finally:
        db.close()

    print(
        f"[add-venue] Done. Venue: {venue.name}, "
        f"happy hours saved: {inserted}"
    )
    for hh in enriched.get("happy_hours", []):
        days = ",".join(hh.get("days") or []) or "?"
        start = hh.get("start_time") or "?"
        end = hh.get("end_time") or "?"
        label = hh.get("label", "Happy Hour")
        print(f"    - {label}: {days} {start}-{end} ({hh.get('source')})")

    return {"venue": venue.name, "happy_hours": inserted}


def main() -> int:
    parser = argparse.ArgumentParser(description="Manually add a venue.")
    parser.add_argument("--maps-url", help="Google Maps place URL (preferred).")
    parser.add_argument("--website", help="Venue website URL.")
    parser.add_argument("--name", help="Venue name (required if --maps-url is omitted).")
    parser.add_argument(
        "--address", help="Venue address (required if --maps-url is omitted)."
    )
    parser.add_argument("--latitude", type=float, help="Optional latitude.")
    parser.add_argument("--longitude", type=float, help="Optional longitude.")
    parser.add_argument("--phone", help="Optional phone number.")
    parser.add_argument(
        "--show-browser",
        action="store_true",
        help="Run with a visible Chrome window (default: headless).",
    )
    args = parser.parse_args()

    if args.maps_url:
        result = add_from_maps_url(args.maps_url, headless=not args.show_browser)
    elif args.website and args.name and args.address:
        result = add_from_website(
            website=args.website,
            name=args.name,
            address=args.address,
            latitude=args.latitude,
            longitude=args.longitude,
            phone=args.phone,
        )
    else:
        parser.error(
            "Provide either --maps-url, OR (--website AND --name AND --address)."
        )

    print(f"\nResult: {result}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
