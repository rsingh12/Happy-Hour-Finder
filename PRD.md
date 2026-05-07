# Happy Hour Finder & Social Planner — Product Requirements

> **How to use this document:** A PRD exists so everyone building the product
> agrees on *what* we're building and *why* before anyone writes code. The
> clearer the PRD, the fewer mid-project pivots. Each section below has a
> purpose — keep them short, concrete, and decision-oriented.

---

## 1. Overview

One paragraph. What is the product, who uses it, what problem does it solve?

**Example:** Happy Hour Finder is a Windows CLI tool that helps a single user
discover happy hour deals at bars and restaurants near them, adds the ones they
like to a dedicated Google Calendar as recurring events, and kicks off a
WhatsApp group with a preset list of friends to coordinate meetups. The goal is
cost-effective socializing with minimal manual effort — no more typing "happy
hour near me" into Google every Friday afternoon.

---

## 2. Goals

Bulleted list of measurable outcomes. Start each with an action verb. Keep it
to 3–5 goals — more than that means you're not prioritizing.

- Discover 15–20 venues with real happy hour info per scan, within a
  user-specified search radius.
- Extract accurate days of the week and start/end times for each happy hour
  without requiring the user to enter them manually.
- Add selected happy hours as recurring weekly events on a dedicated Google
  Calendar, with venue address, specials, and website in the event description.
- Create a WhatsApp group with friends from a config file and post the chosen
  happy hours as the first message, in under 2 minutes end-to-end.
- Work for a single user on Windows 11 with Python 3.14 and Chrome installed.

---

## 3. Non-Goals

Equally important. Every non-goal saves you from a tangent. Spell out what
you're *not* building so you don't accidentally build it.

- Not a multi-user app. No accounts, no auth beyond personal OAuth.
- Not a happy hour aggregator with a hosted database. Data is fetched live each
  scan and cached locally.
- Not a mobile app. CLI only, desktop only.
- Not a reservation or payment system. Booking and ordering happen at the venue.
- Not a recommendation engine. The user picks venues manually — no ranking,
  personalization, or ML.
- Not localizable. US English, USD, 24-hour time internally but
  region-agnostic display.

---

## 4. Users & Context

Who is this for? What's their environment? What do they already have?

**Primary user:** One person (the owner), running the tool on a personal
Windows machine. Has a Google account, a WhatsApp account on their phone, a
Chrome browser, and a list of ~3–10 friends they want to hang out with
regularly.

**Environment:**
- Windows 11, Python 3.14, Chrome (latest).
- Google account with Calendar API access (credentials.json provided by user).
- Anthropic API key for LLM-based scraping fallback.
- Terminal-based usage (PowerShell or cmd).

**Context of use:** Run ad-hoc, usually Friday afternoon or mid-week when the
user is thinking about going out. Not a background service.

---

## 5. User Stories

Each story should follow: *As a [user], I want [action], so that [outcome].*
Write 4–8 top-level stories. Each one maps to a concrete menu command or flow.

- As the user, I want to scan Google Maps for nearby bars with happy hours so I
  don't have to search them manually.
- As the user, I want the tool to extract days and times automatically (even
  from image-based menus) so I never have to type in happy hour details.
- As the user, I want to pick which venues and which happy hours go on my
  calendar, so I stay in control of what clutters it.
- As the user, I want recurring calendar events so I only have to add a venue
  once to see it every week.
- As the user, I want to create a WhatsApp group with my friends and
  auto-send the happy hour details, so I don't have to copy-paste into chats.
- As the user, I want to wipe all calendar events created by this tool, so I
  can re-test without manual cleanup.
- As the user, I want configurable settings (location, radius, max results,
  preferred days/times) so I can tune the tool without editing code.

---

## 6. Functional Requirements

The bulk of the document. Group by feature area. For each, say **what** it
does, **inputs**, **outputs**, and any **rules**. Avoid prescribing
implementation unless it really matters.

### 6.1 Scanning

- Search Google Maps for happy hour venues near a user-specified location.
- Inputs from `config/settings.json`: search query string, max results (default
  20), search radius in miles.
- Outputs to `data/scanned_places.json`: name, address, rating, phone, website
  URL, Google Maps URL per place.
- Must extract the website URL — if missing, the venue is unusable for
  downstream steps.

### 6.2 Happy Hour Extraction

- For each scanned venue, fetch the venue's website and extract happy hour
  details: days of the week, start time, end time, specials (food/drinks with
  prices).
- Must crawl at least 1 level deep (homepage + up to 5 linked sub-pages whose
  URLs/link text mention menu/drinks/specials/happy-hour/etc.).
- Must handle the case where happy hour info is inside an image
  (e.g., a Squarespace-hosted PNG of a drink menu) — use a vision model
  fallback.
- Must reject time ranges that are implausibly long (> 5.5 hours or < 30 min),
  which are almost always restaurant operating hours leaking into the match.
- Must handle venues with multiple distinct happy hours (e.g., weekday 4–6pm
  *and* late-night 9–11pm) — each becomes its own entry.
- Must skip venues where no usable happy hour info can be found (no time range
  AND no days of week).
- Output stored in `data/happy_hours.json`, keyed by venue.

### 6.3 Calendar Integration

- Authenticate to Google Calendar via OAuth 2.0 Desktop flow. Token cached
  locally.
- Must create (or reuse) a dedicated calendar named "Happy Hour" (configurable)
  — never pollute the user's primary calendar.
- Must create one recurring weekly event per selected (venue, happy hour,
  day-of-week) combination.
- Event title: `🍻 {venue_name} - {happy_hour_label}`.
- Event description: specials, phone, website, Google Maps URL.
- Event location: venue address (so Google Maps can open it from the calendar).
- Must include a helper to delete all events from the Happy Hour calendar (for
  testing), without touching the rest of the user's calendars.

### 6.4 WhatsApp Group Creation

- Read friends list from `config/friends.json` (name + E.164 phone number).
- Use WhatsApp Web via Selenium with a persistent Chrome profile so the QR
  scan is a one-time event.
- Create a new group with a configurable name, add all friends as members,
  post a formatted message containing the selected happy hour details.
- Message format: venue name, address, days, times, specials, and Google Maps
  URL per venue.
- Must gracefully skip friends whose phone numbers aren't in the user's
  WhatsApp contacts, and surface which were skipped.

### 6.5 CLI Menu

Interactive menu with these commands:

1. Scan for nearby happy hours
2. Browse discovered deals
3. Add happy hours to Google Calendar
4. Create WhatsApp group & invite friends
5. View upcoming calendar events
6. View settings
7. Full flow (scan → calendar → WhatsApp)
8. Delete all happy hour calendar events (testing)
9. Exit

### 6.6 Configuration

Two JSON files under `config/`:

- `friends.json` — list of `{name, phone}` + group name
- `settings.json` — location, query, radius, max results, preferred days/times,
  calendar name

On first run, if either is missing, print a clear error with an example.

---

## 7. Non-Functional Requirements

Things that aren't features but affect whether the product is usable.

- **Cost ceiling:** A full scan must cost less than $0.25 in LLM API calls.
- **Latency:** A 20-venue scan should complete in < 10 minutes on a typical
  residential connection.
- **Encoding:** All file I/O must use UTF-8 (scraped content contains emoji,
  curly quotes, accented characters — cp1252 on Windows breaks this).
- **Reliability:** If a single venue fails (bad website, timeout, LLM error),
  the scan must continue with the remaining venues — never crash the whole run.
- **Idempotency:** Re-running a scan must overwrite previous data, not append.
- **Safety:** The calendar delete command must only touch the "Happy Hour"
  calendar, never any other calendar the user owns.
- **Privacy:** Friends' phone numbers live only on disk in the user's config
  directory. They are never sent to any API.

---

## 8. Data Model

Sketch the shape of the data the system produces/consumes. This prevents
ambiguity later.

### happy_hours.json (output of extraction)

```json
[
  {
    "name": "Bar 101 Eats & Drinks",
    "address": "101 Main St, Roseville, CA 95678",
    "website": "https://bar101.example",
    "maps_url": "https://www.google.com/maps/place/...",
    "phone": "+1 916-555-0100",
    "rating": "4.6 stars",
    "happy_hours": [
      {
        "label": "Happy Hour",
        "days": ["Monday", "Tuesday", "Wednesday", "Thursday", "Friday"],
        "start_time": "15:00",
        "end_time": "18:00",
        "specials": ["$5 house wine", "$4 draft beer", "half-price wings"],
        "confidence": "high",
        "source": "website_regex"
      }
    ]
  }
]
```

Key points:
- Times are **always** 24-hour "HH:MM" strings.
- Days are **always** full capitalized day names.
- `source` is one of: `website_regex`, `llm`, `vision`.
- Multiple happy hours at the same venue = multiple entries in the array.

---

## 9. External Dependencies

- **Google Maps** — scraped via Selenium (no official free API for this).
- **Google Calendar API** — user provides OAuth credentials.
- **Anthropic API (Claude Haiku)** — LLM text + vision extraction fallback.
- **WhatsApp Web** — automated via Selenium with persistent profile.
- **Python libraries:** selenium, webdriver-manager, beautifulsoup4, requests,
  google-api-python-client, google-auth-oauthlib, inquirer, anthropic.

---

## 10. Out of Scope (v1)

Write this *before* you start coding. Saves pain.

- PDF menu extraction (many venues use PDFs — not handling yet).
- Yelp/Foursquare/OpenTable integration.
- Multi-user accounts.
- Scheduling the scan to run automatically.
- Push notifications / reminders beyond Google Calendar's built-in reminders.
- Rating or ranking venues (no "best happy hour" scoring).
- Any form of payment/booking.

---

## 11. Success Criteria

How you know v1 is done. Measurable.

- ✅ A fresh scan of 20 venues near zip 95747 surfaces **at least 10 venues
  with complete, accurate happy hour info** (days + times + at least one
  special).
- ✅ The user can go from "run the program" to "calendar events created and
  WhatsApp group messaged" in **under 5 minutes**, excluding the one-time
  OAuth/QR setup.
- ✅ The extracted times **match the actual venue's happy hour** for at least
  9 of the top 10 venues (spot-checked manually).
- ✅ Re-running the scan does not create duplicate calendar events.
- ✅ Cost per full scan stays under $0.25.

---

## 12. Open Questions

A PRD is rarely complete. List what's still unknown — this becomes the agenda
for the first clarifying conversation. Keep this list short and specific.

- How should the tool handle venues that host their menu as a PDF instead of
  an image?
- Should the WhatsApp message include a map link, or just the venue address?
- If a venue has happy hour info but no days specified (just times), should
  the tool default to weekdays or ask the user?
- How should the tool handle happy hours that wrap past midnight (e.g.,
  10pm–1am)? Currently handled by converting end time to next-day, but
  this needs verification in the calendar UI.
- When a happy hour has multiple time ranges for the same days (rare but
  possible), should they become separate events or one merged event?

---

## 13. Rollout Plan

Even for a solo project, thinking about rollout makes you prioritize.

1. **v0 (scaffold):** Project structure, configs, menu, Google Calendar auth
   flow. No real data extraction.
2. **v1 (minimum viable):** Google Maps scan + regex-based extraction. Expect
   low accuracy.
3. **v1.1 (LLM text fallback):** Add Claude Haiku for venues regex can't
   parse. Accuracy jumps significantly.
4. **v1.2 (vision fallback):** Add Claude vision for image-based menus. Covers
   the long tail of prettified Squarespace sites.
5. **v1.3 (sanity filters):** Reject implausible time ranges, skip empty
   venues, show clear status in UI.
6. **v2 (polish):** PDF handling, better WhatsApp group error messages, retry
   logic for flaky sites.

---

## 14. Appendix: Writing Effective Requirements

A few rules of thumb you can apply to any PRD you write in the future:

- **Use the smallest number of words.** If a requirement needs a paragraph,
  it's probably two requirements.
- **State outcomes, not implementations.** "Extract happy hour times from
  images" is a requirement. "Use Claude vision API" is an implementation.
- **Be specific about numbers.** "Fast" is not a requirement. "< 10 minutes for
  20 venues" is.
- **Say what you won't build.** The non-goals list is often more valuable than
  the goals list.
- **Give examples.** One concrete example of a data structure or user flow
  prevents 50 back-and-forth questions.
- **Separate must from nice-to-have.** If everything is required, nothing is.
- **Version it.** "v1 does X. v2 adds Y." This lets you ship earlier.
- **List open questions.** Don't pretend you have all the answers.
