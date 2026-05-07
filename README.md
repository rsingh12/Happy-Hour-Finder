# Happy Hour

**Find nearby happy hour deals, share them with friends, and turn "where should we go tonight?" into a one-tap plan.**

A multi-stage project that started as a Windows Python CLI for scraping
local restaurant happy hours and is now evolving into a multi-user iOS
app with social RSVPs and a FastAPI + Postgres backend.

> **Status:** v0 CLI shipped. v1 backend (auth + venue discovery + happy
> hour extraction) running locally. iOS app and outings/invitations are
> in flight — see the [PRD](./PRD.md) for full scope.

---

## Why this project

Most "happy hour aggregator" apps either have stale data or only cover
big cities. The interesting engineering problem is **automating the
extraction of accurate days, times, and specials** from restaurant
websites — which are wildly inconsistent: image-based menus,
PDF-only specials, social-hour-by-another-name, day ranges in 12-hour
ambiguous formats, etc.

This project builds the data pipeline first and the consumer app on top.

---

## Architecture

### Discovery and extraction (today)

```
┌───────────────────┐    ┌───────────────────┐    ┌──────────────────┐
│  Yelp Fusion API  │───▶│  Website Crawler  │───▶│ Claude Haiku LLM │
│  (POI discovery)  │    │  (BFS, 2 levels)  │    │  Text + Vision   │
└───────────────────┘    └───────────────────┘    └────────┬─────────┘
                                                            │
                                  ┌─────────────────────────┘
                                  ▼
                       ┌────────────────────┐
                       │   Postgres + ORM   │
                       │ venues, hh, users  │
                       └────────┬───────────┘
                                │
                                ▼
                       ┌────────────────────┐
                       │   FastAPI server   │
                       │ /auth, /venues,    │
                       │ /outings (planned) │
                       └────────────────────┘
```

**Discovery (`yelp_discovery.py`):** Yelp's free Fusion API queried
across ~40 categories *and* free-text terms ("happy hour", "social
hour", "drink specials") to maximize recall. Selenium-based Google
Maps scraper as fallback when no Yelp key is configured.

**Crawling (`happy_hour_parser.py`):** BFS over the venue's homepage,
following links scored by happy-hour-related keywords (including links
hidden in query strings like `/menu?page=happy`). Crawls up to 2
levels deep, capped at 8 pages per venue.

**Extraction (`llm_extractor.py`):** Claude Haiku as the primary
extractor — handles ambiguous time formats, Social Hour-style synonyms,
and rejects loyalty-program noise that regex would match. Vision
fallback (Claude with image input) for venues whose menus are PNGs or
JPEGs (very common on Squarespace sites).

**Sanity rules:** Reject implausible time ranges (>5.5 hours of duration
= probably operating hours), reject AM-only ranges (probably an AM/PM
parse error), require both days AND start/end time before persisting.

### Planned (v1 in progress)

- **iOS app** in Swift + SwiftUI: auth, location-aware venue browsing,
  RSVP/invitation flow, EventKit calendar integration, iMessage
  deep-link for group coordination
- **Outings + invitations:** lead user picks a date and venue, invites
  friends; on accept, event lands in their iOS Calendar (which syncs
  to their email calendar)
- **Push notifications** via APNs for invitation/response events
- **TestFlight** distribution for beta testers

---

## Tech stack

**Backend:** FastAPI · SQLAlchemy 2.0 · Alembic · Postgres 16 · Docker · pydantic-settings · bcrypt + JWT · python-jose · uvicorn

**Discovery & extraction:** Yelp Fusion API · Selenium + webdriver-manager · BeautifulSoup · httpx · Claude API (Haiku, text + vision) · pytest

**Planned client:** Swift · SwiftUI · CoreLocation · EventKit · MessageUI · APNs · MapKit

---

## Repository structure

```
happy-hour/
├── backend/                  # FastAPI server (v1, in progress)
│   ├── app/
│   │   ├── auth/             # registration, login, JWT, email verification
│   │   ├── venues/           # geo-aware venue search endpoints
│   │   └── scanner/          # Yelp discovery + LLM extraction pipeline
│   ├── alembic/              # Postgres migrations
│   └── docker-compose.yml    # local Postgres
├── src/                      # v0 single-user Windows CLI (legacy reference)
│   ├── scanner.py            # Selenium-based Google Maps scraper
│   ├── happy_hour_parser.py  # crawler + LLM extractor
│   ├── calendar_sync.py      # Google Calendar API integration
│   └── whatsapp_bot.py       # WhatsApp Web automation (deprecated)
├── PRD.md                    # full product requirements doc
├── setup_guide.md            # v0 CLI setup
└── README.md
```

---

## Running it locally

This is a multi-component project. Quickest path to seeing it work:

### 1. Start Postgres

```sh
cd backend
docker compose up -d
```

### 2. Set up the API

```sh
python -m venv .venv
.\.venv\Scripts\activate.bat   # Windows
# OR
source .venv/bin/activate      # Mac/Linux

pip install -r requirements.txt
cp .env.example .env
# Edit .env: set ANTHROPIC_API_KEY and YELP_API_KEY
alembic upgrade head
uvicorn app.main:app --reload --port 8000
```

Open http://localhost:8000/docs for the Swagger UI.

### 3. Populate the database with real venues

```sh
python -m app.scanner.worker --lat 38.7521 --lng -121.2966 --radius 25 --max 150
```

A typical run takes 30-45 minutes and surfaces ~30-50 venues with
complete happy hour data. Cost: ~$3-8 in Anthropic API calls per scan.

### 4. Query venues

```sh
curl "http://localhost:8000/venues?lat=38.7521&lng=-121.2966&radius_miles=10&day=Friday&start_time=15:00&end_time=20:00" \
  -H "Authorization: Bearer <jwt>"
```

Detailed setup walkthrough including the v0 CLI flow:
[`setup_guide.md`](./setup_guide.md). Detailed product scope and
roadmap: [`PRD.md`](./PRD.md).

---

## Engineering decisions worth calling out

A few choices made along the way that I'd defend:

- **LLM-first extraction over regex.** Original v0 used regex for
  speed/cost. Real-world websites are messy enough (loyalty programs
  worded as `$5 OFF`, image-based menus, "social hour" synonyms) that
  regex hit a recall ceiling quickly. Switched to Claude Haiku as the
  primary extractor with regex as fallback when no API key is set.
  ~$0.05-0.15 per venue, well worth it.

- **Local-first development.** Backend and Postgres both run on the
  same machine via Docker for the whole v1 development cycle. Free,
  fast, offline-friendly. Deferred cloud deploy to v2 for cost (Render
  $0/mo on free tier, Supabase $0/mo on free tier when we move).

- **Strict data-quality filter at ingest.** A happy hour without
  start/end time is unactionable for a calendar event, so the parser
  rejects incomplete entries before they reach Postgres rather than
  storing them as "TBD." Kept the data model honest.

- **Bounded BFS for site crawling.** Restaurant websites bury HH info
  at varied depths (`/happy-hour`, `/menu?page=happy`,
  `/locations/<city>/menu?page=happy`). Crawler does up to 2 levels
  deep with a hard cap of 8 pages per venue and keyword-weighted
  link scoring. Catches the common cases without exploding scan time.

- **Two-stage dedupe.** Within a venue, entries with identical
  `(days, start, end, label)` get merged outright. Entries with
  identical `(start, end, label)` but different day sets get unioned
  (catches LLM splitting "Mon–Fri" into multiple rows that mean the
  same thing).

---

## Roadmap

- [x] **v0** — single-user Windows CLI: scan → curate → Google Calendar
- [x] **v1 backend (Phase 1)** — auth, venue + happy-hour DB, Yelp +
      Selenium discovery, LLM extraction, `/venues` API
- [ ] **v1 backend (Phase 2)** — outings, friends, invitations,
      APNs push notifications
- [ ] **v1 iOS app** — Swift/SwiftUI client, EventKit, iMessage compose
- [ ] **v1.5** — feedback collection, attendance tracking
- [ ] **v2** — preference learning, personalized recommendations,
      multi-city expansion

---

## License

MIT — see `LICENSE` (to be added).
