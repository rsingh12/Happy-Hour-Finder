"""
String normalization and identifier extraction for robust venue matching.

The canonical identity key is `google_place_id` (extracted from the `/g/...`
segment of a Google Maps place URL). When that is missing, fall back to
(normalized_name + geo proximity) or (normalized_name + street_number + zip).
"""

from __future__ import annotations

import re
import unicodedata
import urllib.parse
from typing import Optional

# Common business-name suffixes/qualifiers that vary between scrapes but don't
# change the venue's identity ("Twain's Tavern" vs "Twains Tavern Inc.").
_NAME_NOISE_RE = re.compile(
    r"\b(inc|llc|ltd|co|corp|restaurant|restaurants|bar|grill|grille|"
    r"pub|tavern|lounge|cafe|kitchen|company|the)\b\.?",
    re.IGNORECASE,
)

# Street-suffix canonicalization for addresses.
_STREET_SUFFIX = {
    "boulevard": "blvd",
    "avenue": "ave",
    "av": "ave",
    "street": "st",
    "road": "rd",
    "drive": "dr",
    "lane": "ln",
    "parkway": "pkwy",
    "highway": "hwy",
    "place": "pl",
    "court": "ct",
    "circle": "cir",
    "terrace": "ter",
}

_SUITE_RE = re.compile(r"\b(suite|ste|unit|apt|#)\s*\S+", re.IGNORECASE)
_ZIP_RE = re.compile(r"\b(\d{5})(?:-\d{4})?\b")


def normalize_name(name: Optional[str]) -> str:
    if not name:
        return ""
    s = unicodedata.normalize("NFKD", name).encode("ascii", "ignore").decode("ascii")
    s = s.lower()
    # Apostrophes drop ("Twain's" -> "twains"); other punctuation becomes
    # whitespace. The asymmetry preserves token identity for contractions.
    s = re.sub(r"['’]", "", s)
    s = re.sub(r"[^\w\s]", " ", s)
    s = _NAME_NOISE_RE.sub(" ", s)
    s = re.sub(r"\s+", " ", s).strip()
    return s


def normalize_address(address: Optional[str]) -> str:
    """Full normalized address — useful as a coarse equality check."""
    if not address:
        return ""
    s = unicodedata.normalize("NFKD", address).encode("ascii", "ignore").decode("ascii")
    s = s.lower()
    s = s.replace(",", " ").replace(".", " ").replace("-", " ")
    s = _SUITE_RE.sub(" ", s)
    parts = [_STREET_SUFFIX.get(p, p) for p in s.split()]
    s = " ".join(parts)
    s = re.sub(r"\s+", " ", s).strip()
    return s


def extract_street_number(address: Optional[str]) -> str:
    if not address:
        return ""
    m = re.match(r"\s*(\d+)\b", address)
    return m.group(1) if m else ""


def extract_zip(address: Optional[str]) -> str:
    if not address:
        return ""
    m = _ZIP_RE.search(address)
    return m.group(1) if m else ""


# `/g/...` in the `!16s` segment is the modern Places-API-compatible Place ID;
# the legacy `!1s<feature>:<cid>!` form encodes the same place but is less
# canonical. URL-encoded form (`%2Fg%2F`) is what Selenium typically returns.
_PLACE_ID_RE = re.compile(r"!16s(%2Fg%2F[^!?&]+)")
_PLACE_ID_RE_PLAIN = re.compile(r"!16s(/g/[^!?&]+)")


def extract_google_place_id(maps_url: Optional[str]) -> Optional[str]:
    """Extract the durable `/g/...` Place ID from a Google Maps URL."""
    if not maps_url:
        return None
    m = _PLACE_ID_RE.search(maps_url) or _PLACE_ID_RE_PLAIN.search(maps_url)
    if not m:
        return None
    return urllib.parse.unquote(m.group(1))


