"""
Scrapes and parses happy hour information from restaurant/bar websites and Google listings.
"""

import json
import re
from pathlib import Path
from datetime import time as dt_time
from urllib.parse import urljoin, urlparse

import requests
from bs4 import BeautifulSoup

from app.scanner.llm_extractor import (
    extract_with_llm,
    extract_with_vision,
    is_available as llm_available,
)


DATA_DIR = Path(__file__).parent.parent / "data"

SUBPAGE_KEYWORDS = [
    "happy-hour", "happyhour", "happy_hour",
    "social-hour", "socialhour", "social_hour", "the-social-hour",
    "late-night", "twilight-hour", "sunset-hour", "power-hour", "after-work",
    "specials", "special",
    "menu", "drink", "drinks", "bar", "cocktail", "deal", "deals",
    "promo", "promotion",
]

# Filename/alt-text keywords that suggest an image contains happy hour info
IMAGE_KEYWORDS = [
    "happy", "hour", "social", "twilight", "sunset", "late-night",
    "drink", "menu", "special", "bar", "cocktail", "deal", "promo",
]

HTTP_HEADERS = {
    "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
                  "AppleWebKit/537.36 (KHTML, like Gecko) "
                  "Chrome/120.0.0.0 Safari/537.36",
    "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
    "Accept-Language": "en-US,en;q=0.5",
}

DAY_NAMES = ["Monday", "Tuesday", "Wednesday", "Thursday", "Friday", "Saturday", "Sunday"]
DAY_ABBREVS = {
    "mon": "Monday", "tue": "Tuesday", "tues": "Tuesday", "wed": "Wednesday",
    "thu": "Thursday", "thur": "Thursday", "thurs": "Thursday",
    "fri": "Friday", "sat": "Saturday", "sun": "Sunday",
}

HAPPY_HOUR_KEYWORDS = [
    # Direct synonyms for "happy hour"
    "happy hour", "social hour", "the social hour",
    "late night", "late-night", "twilight hour", "sunset hour",
    "power hour", "after work hour", "after-work hour",
    # Promotional language that often accompanies these blocks
    "drink special", "food special", "half price", "half-price",
    "2 for 1", "two for one", "buy one get one", "bogo",
    "discounted drinks", "bar specials", "daily special", "weekly special",
    "$2", "$3", "$4", "$5", "$6", "$7",
]

TIME_PATTERN = re.compile(
    r"(\d{1,2})\s*(?::(\d{2}))?\s*(am|pm|a\.m\.|p\.m\.)?",
    re.IGNORECASE,
)

TIME_RANGE_PATTERN = re.compile(
    r"(\d{1,2}(?::\d{2})?\s*(?:am|pm|a\.m\.|p\.m\.)?)"
    r"\s*[-–—to]+\s*"
    r"(\d{1,2}(?::\d{2})?\s*(?:am|pm|a\.m\.|p\.m\.)?)",
    re.IGNORECASE,
)

DAY_RANGE_PATTERN = re.compile(
    r"(mon(?:day)?|tue(?:s(?:day)?)?|wed(?:nesday)?|thu(?:r(?:s(?:day)?)?)?|fri(?:day)?|sat(?:urday)?|sun(?:day)?)"
    r"\s*[-–—through]+\s*"
    r"(mon(?:day)?|tue(?:s(?:day)?)?|wed(?:nesday)?|thu(?:r(?:s(?:day)?)?)?|fri(?:day)?|sat(?:urday)?|sun(?:day)?)",
    re.IGNORECASE,
)


def parse_time_string(s):
    """Parse a time string like '4pm', '4:30 PM', '16:00' into (hour, minute)."""
    s = s.strip()
    m = TIME_PATTERN.search(s)
    if not m:
        return None
    hour = int(m.group(1))
    minute = int(m.group(2)) if m.group(2) else 0
    ampm = (m.group(3) or "").lower().replace(".", "")
    if ampm == "pm" and hour < 12:
        hour += 12
    elif ampm == "am" and hour == 12:
        hour = 0
    if 0 <= hour <= 23 and 0 <= minute <= 59:
        return (hour, minute)
    return None


def expand_day_range(start_day, end_day):
    """Expand 'Mon-Fri' into list of day names."""
    start_key = start_day.lower()[:3]
    end_key = end_day.lower()[:3]
    start_name = DAY_ABBREVS.get(start_key, start_day.title())
    end_name = DAY_ABBREVS.get(end_key, end_day.title())
    try:
        si = DAY_NAMES.index(start_name)
        ei = DAY_NAMES.index(end_name)
    except ValueError:
        return [start_name, end_name]
    if ei >= si:
        return DAY_NAMES[si : ei + 1]
    return DAY_NAMES[si:] + DAY_NAMES[: ei + 1]


def extract_happy_hour_from_text(text):
    """Extract happy hour details from free-form text.

    Looks at a window of ±3 lines around each 'happy hour' mention so that
    timing and days found on adjacent lines still get associated with the
    same entry.
    """
    results = []
    lines = [ln.strip() for ln in text.split("\n") if ln.strip()]
    window = 3

    for i, line in enumerate(lines):
        line_lower = line.lower()
        if not any(kw in line_lower for kw in HAPPY_HOUR_KEYWORDS):
            continue

        # Build a context block around this line
        start = max(0, i - window)
        end = min(len(lines), i + window + 1)
        block = " ".join(lines[start:end])
        block_lower = block.lower()

        entry = {
            "raw_text": line.strip(),
            "context": block,
            "days": [],
            "start_time": None,
            "end_time": None,
            "specials": [],
        }
        # Run the subsequent regexes against the wider block, not just the line
        line = block
        line_lower = block_lower

        # Extract day ranges
        day_range_match = DAY_RANGE_PATTERN.search(line)
        if day_range_match:
            entry["days"] = expand_day_range(day_range_match.group(1), day_range_match.group(2))
        else:
            for abbrev, full_name in DAY_ABBREVS.items():
                if abbrev in line_lower:
                    if full_name not in entry["days"]:
                        entry["days"].append(full_name)
            if "daily" in line_lower or "every day" in line_lower:
                entry["days"] = list(DAY_NAMES)
            if "weekday" in line_lower:
                entry["days"] = DAY_NAMES[:5]
            if "weekend" in line_lower:
                entry["days"] = DAY_NAMES[5:]

        # Extract time ranges
        time_range_match = TIME_RANGE_PATTERN.search(line)
        if time_range_match:
            start = parse_time_string(time_range_match.group(1))
            end = parse_time_string(time_range_match.group(2))
            if start:
                entry["start_time"] = f"{start[0]:02d}:{start[1]:02d}"
            if end:
                entry["end_time"] = f"{end[0]:02d}:{end[1]:02d}"

        # Extract price specials
        price_matches = re.findall(r"\$\d+(?:\.\d{2})?\s+\w+[\w\s]*", line)
        entry["specials"] = [m.strip() for m in price_matches]

        # Also grab descriptive specials
        for kw in ["half price", "half-price", "2 for 1", "bogo", "buy one get one"]:
            if kw in line_lower:
                context = line.strip()
                if context not in entry["specials"]:
                    entry["specials"].append(context)

        results.append(entry)

    return results


def _duration_hours(start_time, end_time):
    """Return duration in hours between HH:MM strings. Handles midnight wrap."""
    if not start_time or not end_time:
        return None
    try:
        sh, sm = [int(x) for x in start_time.split(":")]
        eh, em = [int(x) for x in end_time.split(":")]
    except Exception:
        return None
    start_min = sh * 60 + sm
    end_min = eh * 60 + em
    if end_min <= start_min:
        end_min += 24 * 60  # next-day wrap (e.g., 22:00 - 01:00)
    return (end_min - start_min) / 60.0


def is_realistic_happy_hour(entry):
    """Reject entries whose time range is implausible for a happy hour.

    Three checks:
      1. Duration: 30 min to 5.5 hours. Longer is operating hours or brunch.
      2. Start hour: must be 11am or later. Earlier starts are almost
         always AM/PM parsing errors (LLM saw "4:30" without context and
         defaulted to AM). Real HH never starts before 11am.
      3. Both times present (callers already enforce this, but be safe).
    """
    start = entry.get("start_time")
    end = entry.get("end_time")
    if not start or not end:
        return True

    # AM/PM sanity check on the start hour
    try:
        start_hour = int(start.split(":")[0])
    except (ValueError, AttributeError):
        return True
    if start_hour < 11:
        return False

    duration = _duration_hours(start, end)
    if duration is None:
        return True
    return 0.5 <= duration <= 5.5


def score_entry(entry):
    """Score an entry by how complete its happy hour info is."""
    score = 0
    if entry.get("start_time") and entry.get("end_time"):
        score += 10
    elif entry.get("start_time") or entry.get("end_time"):
        score += 3
    if entry.get("days"):
        score += 5
    score += min(len(entry.get("specials", [])), 3)
    return score


def rank_and_dedupe(entries):
    """Sort entries best-first and drop low-quality duplicates."""
    entries = sorted(entries, key=score_entry, reverse=True)
    seen_raw = set()
    unique = []
    for e in entries:
        key = (e.get("start_time"), e.get("end_time"), tuple(e.get("days", [])))
        if key in seen_raw:
            continue
        seen_raw.add(key)
        unique.append(e)
    return unique


def _fetch(url, timeout=12):
    """Fetch a URL and return BeautifulSoup or None."""
    try:
        resp = requests.get(url, headers=HTTP_HEADERS, timeout=timeout)
        resp.raise_for_status()
        return BeautifulSoup(resp.text, "html.parser")
    except Exception:
        return None


def _extract_text(soup):
    """Strip boilerplate and return plain text."""
    if not soup:
        return ""
    for tag in soup(["script", "style", "noscript"]):
        tag.decompose()
    return soup.get_text(separator="\n", strip=True)


def _find_subpage_links(soup, base_url, max_links=5):
    """Find up to N links on the homepage that likely lead to happy hour info."""
    if not soup:
        return []
    candidates = []
    seen = set()

    for a in soup.find_all("a", href=True):
        href = a["href"]
        full = urljoin(base_url, href)

        # Stay on the same domain
        if urlparse(full).netloc != urlparse(base_url).netloc:
            continue
        if full in seen or full == base_url:
            continue

        link_text = (a.get_text() or "").lower()
        href_lower = full.lower()

        score = 0
        for kw in SUBPAGE_KEYWORDS:
            if kw in href_lower:
                score += 3
            if kw in link_text:
                score += 2
        # Strong signal — a discount-period word anywhere in the URL or link
        # text. Includes query strings: `/menu?page=happy` matches just like
        # `/happy-hour` does.
        for strong_kw in ("happy", "social hour", "twilight", "late night"):
            if strong_kw in href_lower or strong_kw in link_text:
                score += 10
        # Bonus: location-index pages, since the HH menu often lives one
        # hop further (per-location). This biases the BFS toward going
        # deeper into /locations/<city>/menu trees.
        for loc_kw in ("location", "locations"):
            if loc_kw in href_lower:
                score += 4

        if score > 0:
            candidates.append((score, full))
            seen.add(full)

    candidates.sort(reverse=True)
    return [url for _, url in candidates[:max_links]]


def _find_candidate_images(soup, base_url):
    """Find image URLs whose filename or alt text suggests happy hour content."""
    if not soup:
        return []
    found = []
    seen = set()
    for img in soup.find_all("img"):
        src = img.get("src") or img.get("data-src") or ""
        if not src:
            continue
        full = urljoin(base_url, src)
        if full in seen:
            continue

        alt = (img.get("alt") or "").lower()
        url_lower = full.lower()

        score = 0
        # Strong signal: "happy" in filename or alt
        if "happy" in url_lower or "happy" in alt:
            score += 10
        for kw in IMAGE_KEYWORDS:
            if kw in url_lower:
                score += 2
            if kw in alt:
                score += 2

        # Skip tiny icon-like images (heuristic: squarespace asset URLs often huge, that's fine)
        if score > 0 and any(
            full.lower().endswith(ext)
            for ext in [".jpg", ".jpeg", ".png", ".webp", ".gif"]
        ):
            found.append((score, full))
            seen.add(full)

    found.sort(reverse=True)
    return [url for _, url in found[:4]]  # top 4 candidates


def crawl_site_for_text(start_url, max_subpages=5, max_depth=2, max_total_pages=8):
    """Fetch a venue's homepage and crawl relevant sub-pages.

    Uses bounded BFS: starts at the homepage (depth 0), follows scored
    HH-relevant links to depth `max_depth`. The total number of pages
    fetched is capped at `max_total_pages` to keep scans fast.

    The 2-level depth catches cases like:
      /  ->  /locations/roseville/  ->  /locations/roseville/menu?page=happy
    where the HH page sits behind a location index.

    Returns (combined_text, visited_pages, candidate_image_urls).
    """
    if not start_url.startswith("http"):
        start_url = "https://" + start_url

    pages_text = []
    visited = []
    visited_set: set[str] = set()
    images = []
    queue: list[tuple[str, int]] = [(start_url, 0)]

    while queue and len(visited) < max_total_pages:
        url, depth = queue.pop(0)
        if url in visited_set:
            continue
        visited_set.add(url)

        soup = _fetch(url)
        if not soup:
            continue

        text = _extract_text(soup)
        if depth == 0:
            pages_text.append(text)
        else:
            pages_text.append(f"\n\n=== {url} ===\n\n{text}")
        visited.append(url)
        images.extend(_find_candidate_images(soup, url))

        if depth >= max_depth:
            continue

        # On deeper levels, take fewer links to avoid blowing the budget
        budget = max_subpages if depth == 0 else 3
        sub_urls = _find_subpage_links(soup, url, max_links=budget)
        for sub_url in sub_urls:
            if sub_url not in visited_set:
                queue.append((sub_url, depth + 1))

    # Dedupe images while preserving order
    seen = set()
    unique_images = []
    for url in images:
        if url not in seen:
            unique_images.append(url)
            seen.add(url)

    return "\n".join(pages_text), visited, unique_images[:4]


def scrape_website_for_happy_hour(url):
    """Fetch a website (homepage + relevant sub-pages) and extract happy hours.

    Always-LLM strategy (current default):
      1. Crawl homepage + up to 5 relevant sub-pages.
      2. Send the combined text to Claude Haiku for structured extraction.
         Claude is much better than regex at handling marketing fluff,
         loyalty noise, "Social Hour"-style synonyms, and diverse phrasing.
      3. If text LLM finds nothing, try Claude vision on candidate images
         (image-based menus are common — Squarespace, etc.).
      4. If LLM is unavailable (no ANTHROPIC_API_KEY), fall back to regex.
    """
    combined_text, visited, images = crawl_site_for_text(url, max_subpages=5)
    if not combined_text and not images:
        return {"url": url, "visited": [], "happy_hours": []}

    # Step 1: ALWAYS run text LLM if available.
    if llm_available() and combined_text:
        llm_entries = extract_with_llm(combined_text)
        llm_entries = [e for e in llm_entries if is_realistic_happy_hour(e)]
        if llm_entries:
            return {
                "url": url,
                "visited": visited,
                "happy_hours": llm_entries[:5],
            }

    # Step 2: vision fallback on candidate images
    if llm_available() and images:
        print(f"      Text LLM empty — trying vision on {len(images)} image(s)...")
        for img_url in images:
            vision_entries = extract_with_vision(img_url)
            vision_entries = [e for e in vision_entries if is_realistic_happy_hour(e)]
            if vision_entries:
                print(f"        [+] Vision extracted {len(vision_entries)} entries from {img_url}")
                return {
                    "url": url,
                    "visited": visited,
                    "happy_hours": vision_entries[:5],
                    "vision_source_image": img_url,
                }

    # Step 3: regex final fallback (only when LLM is unavailable or both came up empty)
    regex_entries = extract_happy_hour_from_text(combined_text)
    for e in regex_entries:
        e["source"] = "regex"
    regex_entries = [e for e in regex_entries if is_realistic_happy_hour(e)]
    regex_ranked = rank_and_dedupe(regex_entries)
    return {"url": url, "visited": visited, "happy_hours": regex_ranked[:3]}


DAY_ORDER = {
    "Monday": 0, "Tuesday": 1, "Wednesday": 2, "Thursday": 3,
    "Friday": 4, "Saturday": 5, "Sunday": 6,
}


def _sort_days(days: list[str]) -> list[str]:
    """Sort day names in week order (Mon, Tue, ...)."""
    return sorted(days, key=lambda d: DAY_ORDER.get(d, 99))


def _dedupe_happy_hours(entries: list[dict]) -> list[dict]:
    """Collapse duplicates within a venue.

    Two-stage merge:
      1. Exact match on (days, start, end, label) — merge specials.
      2. Same (start, end, label) but different day sets — UNION the
         days. This catches LLM outputs like House of Oliver where the
         same Happy Hour block was split across "Mon,Sat", "Thu,Fri",
         and "Fri,Sat" rows that should be one entry covering all four
         days.
    """
    if not entries:
        return entries

    # Stage 1: exact-match dedupe
    by_full_key: dict[tuple, dict] = {}
    for hh in entries:
        days = tuple(sorted(hh.get("days") or []))
        full_key = (
            days,
            hh.get("start_time"),
            hh.get("end_time"),
            (hh.get("label") or "Happy Hour").strip().lower(),
        )
        if full_key in by_full_key:
            _merge_specials(by_full_key[full_key], hh)
        else:
            by_full_key[full_key] = dict(hh)

    stage1 = list(by_full_key.values())

    # Stage 2: same time + label, different days -> union the days
    by_time_key: dict[tuple, dict] = {}
    for hh in stage1:
        time_key = (
            hh.get("start_time"),
            hh.get("end_time"),
            (hh.get("label") or "Happy Hour").strip().lower(),
        )
        if time_key in by_time_key:
            existing = by_time_key[time_key]
            day_union = set(existing.get("days") or []) | set(hh.get("days") or [])
            existing["days"] = _sort_days(list(day_union))
            _merge_specials(existing, hh)
        else:
            by_time_key[time_key] = dict(hh)

    return list(by_time_key.values())


def _merge_specials(target: dict, source: dict) -> None:
    """Merge source['specials'] into target['specials'], deduped, in-place."""
    existing = list(target.get("specials") or [])
    new = list(source.get("specials") or [])
    seen = set()
    merged = []
    for s in existing + new:
        if s and s not in seen:
            merged.append(s)
            seen.add(s)
    target["specials"] = merged


def parse_all_places(places=None):
    """Parse happy hour info for all scanned places."""
    if places is None:
        input_file = DATA_DIR / "scanned_places.json"
        if not input_file.exists():
            print("No scanned places found. Run scanner.py first.")
            return []
        with open(input_file, encoding="utf-8") as f:
            places = json.load(f)

    enriched = []
    skipped = 0
    for i, place in enumerate(places):
        print(f"[{i+1}/{len(places)}] Parsing: {place['name']}")
        entry = dict(place)
        entry["happy_hours"] = []

        website = place.get("website", "")
        if not website:
            print("    [-] No website listed. Skipping.")
            skipped += 1
            continue

        result = scrape_website_for_happy_hour(website)
        happy_hours = result.get("happy_hours", [])

        # Strict filter: BOTH days AND a complete time range required.
        # Without all three (days, start, end), an entry can't be turned
        # into a recurring calendar event later, so it's not actionable.
        valid = [
            hh for hh in happy_hours
            if hh.get("days")
            and hh.get("start_time")
            and hh.get("end_time")
        ]

        # Dedupe within this venue's entries by (days, start, end, label)
        valid = _dedupe_happy_hours(valid)

        if not valid:
            print("    [-] No complete happy hour info found (need days + start + end). Skipping.")
            skipped += 1
            continue

        entry["happy_hours"] = valid
        entry["visited_pages"] = result.get("visited", [])
        print(f"    [+] Found {len(valid)} happy hour entries")
        for hh in valid:
            days = ",".join(hh.get("days") or []) or "?"
            start = hh.get("start_time") or "?"
            end = hh.get("end_time") or "?"
            src = hh.get("source", "?")
            print(f"        - {days} {start}-{end} ({src})")
        enriched.append(entry)

    # Save enriched data
    DATA_DIR.mkdir(exist_ok=True)
    output_file = DATA_DIR / "happy_hours.json"
    with open(output_file, "w", encoding="utf-8") as f:
        json.dump(enriched, f, indent=2, ensure_ascii=False)

    print(f"\nParsed {len(places)} places. {len(enriched)} have usable happy hour info ({skipped} skipped).")
    print(f"Saved to {output_file}")
    return enriched


if __name__ == "__main__":
    parse_all_places()
