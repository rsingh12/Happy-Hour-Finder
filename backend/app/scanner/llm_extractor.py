"""
LLM-based happy hour extractor using Claude Haiku.

Takes messy scraped website text and returns structured happy hour data.
Uses prompt caching on the system prompt so repeated calls across venues
pay only the cached-read rate (~0.1x of normal input cost).
"""

import json
import os
import re

try:
    from anthropic import Anthropic
    HAS_ANTHROPIC = True
except ImportError:
    HAS_ANTHROPIC = False


MODEL = "claude-haiku-4-5"
MAX_TEXT_CHARS = 15000  # trim long pages before sending

SYSTEM_PROMPT = """You are a data extraction assistant. Your job is to read restaurant and bar website text and extract structured discount-period information.

A "happy hour" is a recurring time block when a venue offers discounted food and drinks. Different venues call this by different names — all of these mean the same thing and should be extracted equally:

  - "Happy Hour"           - "Social Hour"        - "The Social Hour"
  - "Late Night"           - "Late Night Happy Hour"
  - "Twilight Hour"        - "Sunset Hour"
  - "Power Hour"           - "After Work Hour"
  - "Bar Specials"         - "Daily Specials" (when time-bound)

Use the venue's own name in the `label` field — preserve "Social Hour" as "Social Hour", don't normalize it to "Happy Hour".

You will receive raw text scraped from a venue's website. Extract every distinct discount-period offering and return them as a JSON array.

Each entry must have this exact schema:
{
  "days": ["Monday", "Tuesday", ...],       // full day names, empty list if unknown
  "start_time": "HH:MM",                    // 24-hour format, or null if unknown
  "end_time": "HH:MM",                      // 24-hour format, or null if unknown
  "specials": ["$5 margaritas", ...],       // list of food/drink specials with prices if known
  "label": "Happy Hour" | "Social Hour" | "Late Night" | etc.   // EXACT name the venue uses
  "confidence": "high" | "medium" | "low"   // how confident you are in the extraction
}

RULES:
1. A venue may have MULTIPLE distinct offerings (e.g., weekday 4-6pm Happy Hour AND late-night 9-11pm). Return each as a separate entry.
2. Convert all times to 24-hour HH:MM format. "4pm" -> "16:00", "11:30 PM" -> "23:30".
3. Expand day ranges: "Mon-Fri" -> ["Monday","Tuesday","Wednesday","Thursday","Friday"]. "Weekdays" -> same. "Daily" or "every day" -> all 7 days.
4. Only include entries where you have at least a time range OR a day (not just marketing fluff like "join us for happy hour!").
5. If the text mentions a discount period but gives no timing or day info at all, return an empty array [].
6. Do NOT fabricate times or days. If the text is unclear, return an empty array.
7. Do NOT include marketing/loyalty programs ("$300 a year in free food", "members earn $5 off") — only time-bound, recurring drink/food specials.
8. Return ONLY valid JSON. No preamble, no markdown code fences, no explanation.

EXAMPLE INPUT 1:
"Join us for Happy Hour Monday through Friday from 3-6pm! $5 house wine, $4 draft beer, half-price appetizers. Late Night Happy Hour Thursday-Saturday 10pm-midnight with $6 cocktails."

EXAMPLE OUTPUT 1:
[
  {
    "days": ["Monday","Tuesday","Wednesday","Thursday","Friday"],
    "start_time": "15:00",
    "end_time": "18:00",
    "specials": ["$5 house wine","$4 draft beer","half-price appetizers"],
    "label": "Happy Hour",
    "confidence": "high"
  },
  {
    "days": ["Thursday","Friday","Saturday"],
    "start_time": "22:00",
    "end_time": "00:00",
    "specials": ["$6 cocktails"],
    "label": "Late Night Happy Hour",
    "confidence": "high"
  }
]

EXAMPLE INPUT 2:
"The Social Hour. Available daily 3pm-6pm at the bar only. Tapas $7. Sangria pitcher $24. Cava by the glass $9."

EXAMPLE OUTPUT 2:
[
  {
    "days": ["Monday","Tuesday","Wednesday","Thursday","Friday","Saturday","Sunday"],
    "start_time": "15:00",
    "end_time": "18:00",
    "specials": ["$7 tapas","$24 sangria pitcher","$9 cava by the glass"],
    "label": "The Social Hour",
    "confidence": "high"
  }
]
"""


_client = None


def get_client():
    global _client
    if _client is None:
        if not HAS_ANTHROPIC:
            raise RuntimeError(
                "anthropic SDK not installed. Run: pip install anthropic"
            )
        if not os.environ.get("ANTHROPIC_API_KEY"):
            raise RuntimeError(
                "ANTHROPIC_API_KEY environment variable not set.\n"
                "Get a key at https://console.anthropic.com/ and set it:\n"
                "  Windows:  set ANTHROPIC_API_KEY=sk-ant-...\n"
                "  Mac/Linux: export ANTHROPIC_API_KEY=sk-ant-..."
            )
        _client = Anthropic()
    return _client


def extract_with_llm(text, venue_name=""):
    """
    Extract happy hour entries from scraped text using Claude Haiku.

    Returns a list of dicts matching the schema in the system prompt,
    or [] if extraction fails or no happy hour info is found.
    """
    if not text or len(text.strip()) < 20:
        return []

    # Truncate extremely long pages
    if len(text) > MAX_TEXT_CHARS:
        # Focus on regions near a discount-period mention
        hh_indices = [
            m.start() for m in re.finditer(
                r"(happy\s*hour|social\s*hour|late\s*night|twilight\s*hour|"
                r"sunset\s*hour|power\s*hour|after[-\s]?work\s*hour|bar\s*specials)",
                text,
                re.IGNORECASE,
            )
        ]
        if hh_indices:
            # Take a window around each mention
            chunks = []
            for idx in hh_indices[:5]:
                start = max(0, idx - 800)
                end = min(len(text), idx + 1500)
                chunks.append(text[start:end])
            text = "\n---\n".join(chunks)
            if len(text) > MAX_TEXT_CHARS:
                text = text[:MAX_TEXT_CHARS]
        else:
            text = text[:MAX_TEXT_CHARS]

    user_content = f"Venue: {venue_name}\n\nScraped text:\n{text}"

    try:
        client = get_client()
        response = client.messages.create(
            model=MODEL,
            max_tokens=1500,
            system=[
                {
                    "type": "text",
                    "text": SYSTEM_PROMPT,
                    "cache_control": {"type": "ephemeral"},
                }
            ],
            messages=[{"role": "user", "content": user_content}],
        )
    except Exception as e:
        print(f"    [!] LLM call failed: {e}")
        return []

    raw = response.content[0].text.strip() if response.content else ""

    # Strip any accidental code fences
    if raw.startswith("```"):
        raw = re.sub(r"^```(?:json)?\s*", "", raw)
        raw = re.sub(r"\s*```$", "", raw)

    try:
        entries = json.loads(raw)
    except json.JSONDecodeError:
        # Try to find a JSON array in the response
        match = re.search(r"\[.*\]", raw, re.DOTALL)
        if match:
            try:
                entries = json.loads(match.group(0))
            except Exception:
                return []
        else:
            return []

    # Normalize & validate
    if not isinstance(entries, list):
        return []

    valid = []
    for e in entries:
        if not isinstance(e, dict):
            continue
        days = e.get("days") or []
        start = e.get("start_time")
        end = e.get("end_time")
        # Require at least one of (days) or (start+end)
        if not days and not (start and end):
            continue
        valid.append({
            "days": days if isinstance(days, list) else [],
            "start_time": start,
            "end_time": end,
            "specials": e.get("specials") or [],
            "label": e.get("label") or "Happy Hour",
            "confidence": e.get("confidence") or "medium",
            "raw_text": "[extracted by LLM]",
            "source": "llm",
        })

    return valid


VISION_SYSTEM_PROMPT = """You are a data extraction assistant. You will be shown an image that may contain a discount-period menu from a bar or restaurant (drink menu, specials board, or promo graphic).

A discount period is a recurring time block when the venue offers discounted food and drinks. Different venues call it different things — all should be extracted equally:

  - "Happy Hour"           - "Social Hour"        - "The Social Hour"
  - "Late Night"           - "Late Night Happy Hour"
  - "Twilight Hour"        - "Sunset Hour"
  - "Power Hour"           - "After Work Hour"
  - "Bar Specials"

Use the venue's own name in `label` — preserve "Social Hour" as "Social Hour".

Extract every distinct offering visible in the image and return a JSON array.

Each entry must have this schema:
{
  "days": ["Monday", ...],                  // full day names, empty list if unknown
  "start_time": "HH:MM",                    // 24-hour format, null if unknown
  "end_time": "HH:MM",                      // 24-hour format, null if unknown
  "specials": ["$5 house wine", ...],       // food/drink specials with prices
  "label": "Happy Hour" | "Social Hour" | etc.,  // EXACT name visible in the image
  "confidence": "high" | "medium" | "low"
}

RULES:
1. Multiple offerings -> multiple entries.
2. Convert times to 24-hour. "2pm" -> "14:00", "6 PM" -> "18:00".
3. Expand day ranges: "Mon-Fri" -> 5 entries. "Daily" -> all 7 days. "Weekdays" -> Mon-Fri. "Weekends" -> Sat, Sun.
4. Read prices from the image. "$5 wells, $4 drafts" -> include as specials.
5. If the image is NOT a discount/drink menu (e.g., it's a logo, food photo, or interior shot), return an empty array [].
6. If you can only read partial info (e.g., specials but no times), still return the entry — use null for missing fields.
7. Do NOT fabricate times, days, or prices. If something is unreadable, omit it.
8. Return ONLY valid JSON. No preamble, no markdown fences, no explanation.
"""


def extract_with_vision(image_url):
    """
    Send an image URL to Claude vision and extract happy hour info.

    Returns a list of dicts matching the schema, or [] if nothing is found
    or the image isn't a happy hour menu.
    """
    if not image_url:
        return []

    try:
        client = get_client()
        response = client.messages.create(
            model=MODEL,
            max_tokens=1500,
            system=[
                {
                    "type": "text",
                    "text": VISION_SYSTEM_PROMPT,
                    "cache_control": {"type": "ephemeral"},
                }
            ],
            messages=[
                {
                    "role": "user",
                    "content": [
                        {
                            "type": "image",
                            "source": {"type": "url", "url": image_url},
                        },
                        {
                            "type": "text",
                            "text": "Extract all happy hour information from this image as JSON.",
                        },
                    ],
                }
            ],
        )
    except Exception as e:
        print(f"        [!] Vision call failed for {image_url}: {e}")
        return []

    raw = response.content[0].text.strip() if response.content else ""

    if raw.startswith("```"):
        raw = re.sub(r"^```(?:json)?\s*", "", raw)
        raw = re.sub(r"\s*```$", "", raw)

    try:
        entries = json.loads(raw)
    except json.JSONDecodeError:
        match = re.search(r"\[.*\]", raw, re.DOTALL)
        if match:
            try:
                entries = json.loads(match.group(0))
            except Exception:
                return []
        else:
            return []

    if not isinstance(entries, list):
        return []

    valid = []
    for e in entries:
        if not isinstance(e, dict):
            continue
        days = e.get("days") or []
        start = e.get("start_time")
        end = e.get("end_time")
        if not days and not (start and end):
            continue
        valid.append({
            "days": days if isinstance(days, list) else [],
            "start_time": start,
            "end_time": end,
            "specials": e.get("specials") or [],
            "label": e.get("label") or "Happy Hour",
            "confidence": e.get("confidence") or "medium",
            "raw_text": f"[extracted by vision from {image_url}]",
            "source": "vision",
        })

    return valid


def is_available():
    """Check if LLM extraction is usable."""
    return HAS_ANTHROPIC and bool(os.environ.get("ANTHROPIC_API_KEY"))


if __name__ == "__main__":
    # Quick test
    sample = """
    Welcome to our bar! Happy Hour is every weekday from 4pm to 7pm.
    Enjoy $5 house wines, $4 draft beers, and half-price wings.
    We also have Late Night Happy Hour Friday and Saturday 10pm - 12am with $6 cocktails.
    """
    print("Testing LLM extractor...")
    print(f"Available: {is_available()}")
    if is_available():
        result = extract_with_llm(sample, "Test Bar")
        print(json.dumps(result, indent=2))
