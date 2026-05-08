# Happy Hour Backend

FastAPI + Postgres backend for the Happy Hour iOS app. v1 scope: auth,
venue discovery, outings with invitations, push notifications.

## Prerequisites

- Python 3.11+ (tested on 3.14)
- Postgres 16 (native installer — see below) **or** Docker Desktop
- Anthropic API key (for the scanner — optional in week 1)

## Postgres setup — pick ONE

### Option A: Native Postgres installer (recommended on Windows without virtualization)

1. Download **Postgres 16** from EnterpriseDB:
   https://www.enterprisedb.com/downloads/postgres-postgresql-downloads
2. Run the installer with all defaults. When asked:
   - **Password for postgres superuser:** pick something memorable
     (e.g., `postgres`). You'll only need it once.
   - **Port:** leave at 5432.
   - **Locale:** Default.
   - You can uncheck **Stack Builder** at the end — not needed.
3. After install, open the bundled **SQL Shell (psql)** from the Start
   menu. Hit Enter through the prompts (defaults are fine) and enter
   the superuser password when asked.
4. At the `postgres=#` prompt, paste these to create the project's
   user + database:
   ```sql
   CREATE USER happyhour WITH PASSWORD 'happyhour_dev';
   CREATE DATABASE happyhour OWNER happyhour;
   GRANT ALL PRIVILEGES ON DATABASE happyhour TO happyhour;
   \q
   ```
5. Done. Postgres now runs as a Windows service, auto-starts on boot.

### Option B: Docker Desktop (if you have virtualization enabled)

```powershell
docker compose up -d
```

That's it — `docker-compose.yml` creates the user, password, and
database matching the connection string in `.env.example`.

## First-time Python setup

```powershell
cd "C:\Users\ravin\Dropbox\Claude Code\Happy Hour\backend"

# 1. Create a virtual environment and activate it
python -m venv .venv
.\.venv\Scripts\Activate.ps1

# 2. Install dependencies
pip install -r requirements.txt

# 3. Copy env template
copy .env.example .env

# 4. Edit .env. The defaults work for local dev; set at minimum:
#    - JWT_SECRET (generate with: python -c "import secrets; print(secrets.token_urlsafe(48))")
#    - ANTHROPIC_API_KEY (only needed when running the scanner)
#    - SENDGRID_API_KEY (optional — without it, verification codes print to console)
#
#    The default DATABASE_URL works for BOTH the native installer
#    (with the user/db created above) and the Docker option.

# 5. Apply DB migrations
alembic upgrade head
```

## Running the API

```powershell
uvicorn app.main:app --reload --host 0.0.0.0 --port 8000
```

Then open http://localhost:8000/docs for the interactive Swagger UI.

## Smoke test

```powershell
# Health check
curl http://localhost:8000/health

# Register a user
curl -X POST http://localhost:8000/auth/register `
  -H "Content-Type: application/json" `
  -d '{"email":"you@example.com","password":"hunter22hunter","display_name":"You"}'
# Note the access_token in the response.
# When SENDGRID_API_KEY is empty, the verification code is printed to the
# server log.

# Verify email (using the printed code)
curl -X POST http://localhost:8000/auth/verify `
  -H "Content-Type: application/json" `
  -d '{"email":"you@example.com","code":"123456"}'

# Login
curl -X POST http://localhost:8000/auth/login `
  -H "Content-Type: application/json" `
  -d '{"email":"you@example.com","password":"hunter22hunter"}'

# /me with the token
curl http://localhost:8000/auth/me `
  -H "Authorization: Bearer eyJ..."
```

## Project structure

```
backend/
  app/
    main.py              # FastAPI entry point
    config.py            # env-loaded settings (pydantic-settings)
    database.py          # SQLAlchemy session + Base
    models.py            # all ORM models
    auth/
      routes.py          # register, login, verify, /me
      security.py        # bcrypt + JWT
      deps.py            # current_user dependency
      email.py           # SendGrid helper (with dev-mode console fallback)
  alembic/               # DB migrations
  alembic.ini
  docker-compose.yml     # local Postgres
  requirements.txt
  .env.example
```

## Common commands

### If using the native Postgres installer

```powershell
# Connect to the project DB
psql -U happyhour -d happyhour
# (password: happyhour_dev)

# Wipe and reset the database (run as the postgres superuser):
psql -U postgres -c "DROP DATABASE IF EXISTS happyhour;"
psql -U postgres -c "CREATE DATABASE happyhour OWNER happyhour;"
alembic upgrade head

# Stop / start the service (run PowerShell as Administrator)
net stop  postgresql-x64-16
net start postgresql-x64-16
```

### If using Docker

```powershell
# View Postgres logs
docker compose logs -f postgres

# Connect to Postgres CLI
docker exec -it happyhour-postgres psql -U happyhour

# Wipe and reset
docker compose down -v
docker compose up -d
alembic upgrade head
```

### Either way

```powershell
# Generate a new migration (after changing models.py)
alembic revision --autogenerate -m "your description"
alembic upgrade head
```

## Running the scanner (Week 2 Half 1)

The scanner populates the `venues` and `happy_hours` tables. You need
this data before `/venues` returns anything useful.

Prereqs:
- Postgres running (`docker compose ps` shows `happyhour-postgres` healthy)
- API venv activated
- `ANTHROPIC_API_KEY` set in `.env` (always-LLM extraction)
- `YELP_API_KEY` set in `.env` (preferred discovery path; free at
  https://docs.developer.yelp.com/)

### Yelp-based scan (recommended)

```cmd
:: From the backend folder, with venv active

:: Default: Yelp discovery within 10 miles of 95747 (Roseville, CA)
python -m app.scanner.worker

:: Custom location and radius
python -m app.scanner.worker --lat 40.7128 --lng -74.0060 --radius 5

:: Lower max if you want a faster test scan
python -m app.scanner.worker --max 20
```

How it works:
1. Yelp Fusion finds bars/restaurants by category (wine bars, tapas,
   breweries, gastropubs, ...) within the radius.
2. Yelp doesn't reliably return websites, so we look each up via a
   targeted Google Maps query (one shared Selenium session).
3. Each website is crawled (homepage + sub-pages); the combined text
   goes to Claude Haiku for structured extraction. If the text LLM
   returns nothing, candidate images get sent to Claude vision.
4. Results are upserted to Postgres. Re-running replaces happy hours
   with the latest scrape.

A typical run: ~60 venues, ~15-25 minutes, ~$0.50-3 in LLM costs.

### Legacy path (Selenium-based, still available)

If `YELP_API_KEY` isn't set or you pass `--legacy`, the old
Google-Maps-search-based scanner runs instead:

```cmd
:: Forces legacy path with default queries (happy hour, social hour,
:: late night, tapas, breweries, wine bars, gastropubs, sports bars)
python -m app.scanner.worker --legacy

:: With custom queries
python -m app.scanner.worker --legacy --query "happy hour near 10001" --query "social hour near 10001"
```

Less reliable than Yelp but works without an API key.

### Other options

```cmd
:: Show the Chrome browser instead of running headless (debugging)
python -m app.scanner.worker --show-browser
```

The scan ends with a summary like:

```
Summary: {'venues': 47, 'happy_hours': 63, 'skipped': 13}
```

Re-running the worker is idempotent — venues are upserted by
(name, address) and their happy hours are replaced with the latest scrape.

## Manually seed a venue Google Maps missed

Some venues don't surface in Google Maps searches even with broad queries
(category-tagged differently, no "happy hour" in their listing, etc.).
For those, you can add them directly:

```cmd
:: Easiest: paste the Google Maps place URL
python -m app.scanner.add_venue --maps-url "https://www.google.com/maps/place/Teleferic+Barcelona/@..."

:: Or, if you'd rather skip Selenium, provide name + address yourself:
python -m app.scanner.add_venue ^
    --website "https://www.monkscellar.com" ^
    --name "Monks Cellar" ^
    --address "240 Vernon St, Roseville, CA 95678"
```

Either path runs the same website-crawl + happy-hour extraction the bulk
scanner uses, then upserts to Postgres. Use this when:

- A venue you know about doesn't show up in `GET /venues`
- A venue's discount block has a non-obvious name (Social Hour, Twilight Hour, etc.)
- You want to seed a specific spot for testing

## Querying venues

After a scan, hit `GET /venues` from Swagger or curl:

```
GET /venues?lat=38.7521&lng=-121.296&radius_miles=10
```

Optional filters:
- `date=2026-05-01` — only venues with a happy hour on that day-of-week
- `day=Friday`     — explicit day name (overrides `date`)
- `start_time=15:00&end_time=20:00` — only happy hours overlapping this window

Response sorts venues by distance from `(lat, lng)`.

`GET /venues/{id}` returns full venue details with all its happy hours.

## Outings, Friends & Push Notifications (Week 2 Half 2)

After authentication and venue discovery, the app's social layer:
users build a friends list, organize outings at specific venues, invite
friends, and get push notifications about responses.

### Friends

```
GET    /friends                — list this user's friends
POST   /friends                — add by phone or email; pending until they sign up
DELETE /friends/{friendship_id} — remove
```

Adding a friend by phone or email that doesn't yet have an account
creates a "pending" record. When that contact later signs up at
`/auth/register`, the friendship is automatically activated (via the
reconciliation hook in `auth/routes.py`).

### Outings

```
POST   /outings                  — create an outing (organizer is auto-joined as accepted)
GET    /outings                  — list outings the current user is in (organizer or invitee)
GET    /outings/{id}             — outing detail with member statuses
POST   /outings/{id}/invite      — organizer invites users by user_id or by phone/email
POST   /outings/{id}/respond     — invitee accepts or declines
GET    /outings/suggest          — density-based date/time recommendation
```

`/outings/suggest` takes `lat`, `lng`, `radius_miles`, `window_hours`
and returns the (day_of_week, start, end) over the next 7 days with
the most happy-hour-running venues in the area. Pure SQL aggregation,
no ML.

### Devices (push notifications)

```
POST   /devices/register   — register an APNs token + device_id for the current user
```

When an organizer invites someone, the backend fires a push notification
in the background. When an invitee responds, the organizer is notified
similarly.

When `APNS_KEY_PATH` and friends are not set in `.env`, push sends are
**stubbed to console output** (so the rest of the system works
end-to-end without an Apple Developer account). Set the four APNs env
vars when ready to send real pushes — see `app/notifications/apns.py`
for the setup steps.

## Roadmap (per the plan)

- **Week 1:** auth + DB schema ✅
- **Week 2 Half 1:** venues + scanner ✅
- **Week 2 Half 2 (now):** outings + friends + push notifications ✅
- **Week 3-4:** iOS app
- **Week 5:** TestFlight
