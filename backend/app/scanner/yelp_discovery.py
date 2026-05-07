"""
Yelp Fusion API client — venue discovery.

Yelp's POI database is more comprehensive than Google Maps search for our
use case because we can filter by specific categories (wine bars, tapas,
breweries, etc.) and Yelp explicitly tags businesses that have a "happy
hour" attribute.

Limitations:
  - Yelp Fusion's standard endpoints don't reliably return business
    websites. After getting venue lists from Yelp, we do a separate
    website-lookup step (see website_finder.py).
  - Free tier: 5,000 calls/day. Each scan uses ~1 call per category +
    1 detail call per venue, so ~50-150 calls per metro scan. Plenty.
  - TOS: Yelp data shouldn't be cached more than 24 hours for some
    endpoints. Re-running the scanner weekly is fine.

Docs: https://docs.developer.yelp.com/reference
"""

from __future__ import annotations

import time
from typing import Optional

import httpx

from app.config import settings


YELP_API_BASE = "https://api.yelp.com/v3"

# Yelp categories that typically run happy/social hours.
# Many casual restaurants run happy hours too (Dog Haus = "Hot Dogs",
# In-N-Out doesn't but Habit might, etc.) — be generous here.
# Full list at: https://docs.developer.yelp.com/docs/resources-categories
HAPPY_HOUR_CATEGORIES = [
    # Core bars & lounges
    "bars", "wine_bars", "tapasbars", "tapas", "cocktailbars",
    "pubs", "sportsbars", "lounges", "tikibars", "divebars",
    "beerbar", "beergardens", "champagne_bars",
    # Breweries & brewpubs
    "breweries", "brewpubs",
    # American restaurants
    "gastropubs", "newamerican", "tradamerican",
    "burgers", "hotdogs", "sandwiches", "tacos",
    "pizza", "steak", "bbq", "barbeque",
    # International cuisines that often have HH
    "mexican", "italian", "japanese", "seafood",
    "sushi", "ramen", "asianfusion", "thai", "vietnamese",
    "korean", "chinese", "indpak", "mediterranean",
    "greek", "spanish", "latin",
    # Other casual categories worth checking
    "salad", "newcanadian", "creperies", "diners",
]

# Free-text search terms — catches venues whose Yelp listing mentions
# any of these, regardless of category. Crucial for finding venues like
# casual restaurants that don't categorize as bars but DO have HH.
HAPPY_HOUR_TERMS = [
    "happy hour",
    "social hour",
    "drink specials",
    "late night happy hour",
]

MAX_PAGE_SIZE = 50  # Yelp's max
MILES_TO_METERS = 1609.34
YELP_MAX_RADIUS_METERS = 40000  # Yelp's hard cap (~25 miles)


def is_available() -> bool:
    return bool(settings.yelp_api_key)


def _headers() -> dict:
    return {"Authorization": f"Bearer {settings.yelp_api_key}"}


def _search_one_page(
    *,
    lat: float,
    lng: float,
    radius_meters: int,
    categories: Optional[str] = None,
    term: Optional[str] = None,
    limit: int,
    offset: int,
) -> list[dict]:
    """Single page of /businesses/search results."""
    params: dict = {
        "latitude": lat,
        "longitude": lng,
        "radius": radius_meters,
        "limit": limit,
        "offset": offset,
        "sort_by": "rating",
    }
    if categories:
        params["categories"] = categories
    if term:
        params["term"] = term
    try:
        r = httpx.get(
            f"{YELP_API_BASE}/businesses/search",
            headers=_headers(),
            params=params,
            timeout=20.0,
        )
        r.raise_for_status()
    except httpx.HTTPStatusError as e:
        # 400/401/403 = config issue; surface clearly
        body = e.response.text[:200] if e.response is not None else ""
        print(f"  [!] Yelp HTTP {e.response.status_code}: {body}")
        return []
    except Exception as e:
        print(f"  [!] Yelp request failed: {e}")
        return []
    data = r.json()
    return data.get("businesses", [])


def _paginate_search(
    *,
    lat: float,
    lng: float,
    radius_meters: int,
    categories: Optional[str] = None,
    term: Optional[str] = None,
    seen_ids: set[str],
    all_businesses: list[dict],
    max_total: int,
) -> int:
    """
    Paginate through one (term, categories) combination. Mutates
    `seen_ids` and `all_businesses` in place. Returns number of NEW
    venues added.
    """
    added = 0
    offset = 0
    while offset < 240 and len(seen_ids) < max_total:
        limit = min(MAX_PAGE_SIZE, max_total - len(seen_ids))
        businesses = _search_one_page(
            lat=lat,
            lng=lng,
            radius_meters=radius_meters,
            categories=categories,
            term=term,
            limit=limit,
            offset=offset,
        )
        if not businesses:
            break

        new_count = 0
        for b in businesses:
            if b.get("id") in seen_ids:
                continue
            seen_ids.add(b["id"])
            all_businesses.append(b)
            new_count += 1
            added += 1

        if new_count == 0 or len(businesses) < limit:
            break
        offset += limit
        time.sleep(0.2)  # gentle rate limit

    return added


def discover_venues(
    *,
    lat: float,
    lng: float,
    radius_miles: float = 10.0,
    max_results: int = 100,
) -> list[dict]:
    """
    Search Yelp via TWO strategies and merge:

      1. Term-based: /businesses/search?term=<happy hour|social hour|...>
         Catches venues whose Yelp listing mentions a discount period
         regardless of their category — including casual restaurants
         that aren't tagged as bars (e.g., Dog Haus, hot dog shops with
         a happy hour page).

      2. Category-based: /businesses/search?categories=<bars,wine_bars,...>
         Catches venues by type, including those whose listing doesn't
         explicitly say "happy hour" but plausibly run one.

    Results are deduped by Yelp business ID.
    """
    if not is_available():
        print("[yelp] YELP_API_KEY not set; skipping Yelp discovery.")
        return []

    radius_meters = min(int(radius_miles * MILES_TO_METERS), YELP_MAX_RADIUS_METERS)
    print(
        f"[yelp] Searching ({lat}, {lng}) within {radius_miles} miles..."
    )

    seen_ids: set[str] = set()
    all_businesses: list[dict] = []

    # Strategy 1: term-based searches (high signal, low cost)
    for term in HAPPY_HOUR_TERMS:
        if len(seen_ids) >= max_results:
            break
        before = len(seen_ids)
        _paginate_search(
            lat=lat,
            lng=lng,
            radius_meters=radius_meters,
            term=term,
            seen_ids=seen_ids,
            all_businesses=all_businesses,
            max_total=max_results,
        )
        added = len(seen_ids) - before
        print(f"  [term: {term!r:30s}] +{added} new venues")

    # Strategy 2: category-based searches (broad type-based coverage)
    chunk_size = 8
    chunks = [
        HAPPY_HOUR_CATEGORIES[i : i + chunk_size]
        for i in range(0, len(HAPPY_HOUR_CATEGORIES), chunk_size)
    ]

    for chunk in chunks:
        if len(seen_ids) >= max_results:
            break
        categories_str = ",".join(chunk)
        before = len(seen_ids)
        _paginate_search(
            lat=lat,
            lng=lng,
            radius_meters=radius_meters,
            categories=categories_str,
            seen_ids=seen_ids,
            all_businesses=all_businesses,
            max_total=max_results,
        )
        added = len(seen_ids) - before
        # Just show first + last category in the chunk to keep logs short
        label = f"{chunk[0]}..{chunk[-1]}" if len(chunk) > 1 else chunk[0]
        print(f"  [cats: {label:30s}] +{added} new venues")

    print(f"[yelp] Discovered {len(all_businesses)} unique venues total.")
    return [_to_place(b) for b in all_businesses]


def get_business_attributes(business_id: str) -> Optional[dict]:
    """
    Fetch full business details for richer attributes (operating hours,
    special_hours, attributes.HappyHour, photos, etc.).

    Returns the raw Yelp response or None on failure.
    """
    if not is_available():
        return None
    try:
        r = httpx.get(
            f"{YELP_API_BASE}/businesses/{business_id}",
            headers=_headers(),
            timeout=20.0,
        )
        r.raise_for_status()
    except Exception as e:
        print(f"  [!] Yelp details failed for {business_id}: {e}")
        return None
    return r.json()


def _to_place(b: dict) -> dict:
    """Convert a Yelp business dict to our internal place format."""
    coords = b.get("coordinates") or {}
    location = b.get("location") or {}
    display_address = location.get("display_address") or []
    return {
        "name": b.get("name", "").strip(),
        "address": ", ".join(display_address),
        "latitude": coords.get("latitude"),
        "longitude": coords.get("longitude"),
        "phone": b.get("display_phone", "") or b.get("phone", ""),
        "rating": b.get("rating"),
        "yelp_id": b.get("id"),
        "yelp_url": b.get("url"),
        "website": "",  # filled in later by website_finder
        "maps_url": "",  # filled in later if we look it up
        "_yelp_categories": [c.get("alias") for c in (b.get("categories") or [])],
    }
