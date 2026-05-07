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

try:
    from llm_extractor import (
        extract_with_llm,
        extract_with_vision,
        is_available as llm_available,
    )
except ImportError:
    from src.llm_extractor import (
        extract_with_llm,
        extract_with_vision,
        is_available as llm_available,
    )


DATA_DIR = Path(__file__).parent.parent / "data"

SUBPAGE_KEYWORDS = [
    "happy-hour", "happyhour", "happy_hour", "specials", "special",
    "menu", "drink", "drinks", "bar", "cocktail", "deal", "deals",
    "promo", "promotion",
]

# Filename/alt-text keywords that suggest an image contains happy hour info
IMAGE_KEYWORDS = [
    "happy", "hour", "drink", "menu", "special", "bar", "cocktail",
    "deal", "promo",
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
    "happy hour", "drink special", "food special", "half price", "half-price",
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
    """Reject entries whose time range is too long to plausibly be a happy hour.

    Restaurant operating hours (e.g., 11am-10pm = 11 hours) often leak into
    regex matches near the phrase 'happy hour'. Real happy hours are typically
    1-5 hours long.
    """
    start = entry.get("start_time")
    end = entry.get("end_time")
    if not start or not end:
        return True  # can't judge without times; let it pass for now
    duration = _duration_hours(start, end)
    if duration is None:
        return True
    # Real happy hours: 30 min to 5.5 hours. Anything longer is almost certainly
    # operating hours, brunch service, or garbage.
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
        # Strong signal
        if "happy" in href_lower or "happy" in link_text:
            score += 10

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


def crawl_site_for_text(start_url, max_subpages=5):
    """Fetch homepage + up to N related sub-pages.

    Returns (combined_text, visited_pages, candidate_image_urls).
    """
    if not start_url.startswith("http"):
        start_url = "https://" + start_url

    pages_text = []
    visited = []
    images = []

    homepage = _fetch(start_url)
    if homepage:
        pages_text.append(_extract_text(homepage))
        visited.append(start_url)
        images.extend(_find_candidate_images(homepage, start_url))

        subpage_urls = _find_subpage_links(homepage, start_url, max_links=max_subpages)
        for sub_url in subpage_urls:
            sub_soup = _fetch(sub_url)
            if sub_soup:
                pages_text.append(f"\n\n=== {sub_url} ===\n\n" + _extract_text(sub_soup))
                visited.append(sub_url)
                images.extend(_find_candidate_images(sub_soup, sub_url))

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

    Strategy:
      1. Regex on combined text.
      2. If regex is incomplete, call Claude text LLM.
      3. If still nothing useful, call Claude vision on candidate images.
    """
    combined_text, visited, images = crawl_site_for_text(url, max_subpages=5)
    if not combined_text and not images:
        return {"url": url, "visited": [], "happy_hours": []}

    # Step 1: regex
    regex_entries = extract_happy_hour_from_text(combined_text)
    for e in regex_entries:
        e["source"] = "website_regex"
    regex_entries = [e for e in regex_entries if is_realistic_happy_hour(e)]
    regex_ranked = rank_and_dedupe(regex_entries)
    regex_good = [e for e in regex_ranked if score_entry(e) >= 8]

    if regex_good:
        return {"url": url, "visited": visited, "happy_hours": regex_good[:5]}

    # Step 2: LLM text fallback
    llm_entries = []
    if llm_available() and combined_text:
        print(f"      Regex incomplete — calling LLM text fallback...")
        llm_entries = extract_with_llm(combined_text)
        llm_entries = [e for e in llm_entries if is_realistic_happy_hour(e)]

    if llm_entries:
        return {"url": url, "visited": visited, "happy_hours": llm_entries[:5]}

    # Step 3: vision fallback on candidate images
    if llm_available() and images:
        print(f"      Text LLM found nothing — trying vision on {len(images)} image(s)...")
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

    # Last resort: return incomplete regex results (may still be useful for display)
    fallback = [e for e in regex_ranked if is_realistic_happy_hour(e)]
    return {"url": url, "visited": visited, "happy_hours": fallback[:3]}


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

        # Only keep entries where we have EITHER days OR a time range
        valid = [
            hh for hh in happy_hours
            if hh.get("days") or (hh.get("start_time") and hh.get("end_time"))
        ]

        if not valid:
            print("    [-] No usable happy hour info found. Skipping.")
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
