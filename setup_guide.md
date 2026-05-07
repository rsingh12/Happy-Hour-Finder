# Happy Hour Finder - Setup Guide

## 1. Install Python Dependencies

```bash
cd "Happy Hour"
pip install -r requirements.txt
```

## 2. Google Calendar API Setup

You need Google Cloud credentials so the app can create calendar events.

### Step-by-step:

1. Go to [Google Cloud Console](https://console.cloud.google.com/)
2. Create a new project (or select existing)
3. In the sidebar: **APIs & Services > Library**
4. Search for **Google Calendar API** and click **Enable**
5. Go to **APIs & Services > Credentials**
6. Click **Create Credentials > OAuth client ID**
7. If prompted, configure the **OAuth consent screen**:
   - User Type: **External**
   - App name: `Happy Hour Finder`
   - Add your email as a test user
8. Back in Credentials, create an **OAuth 2.0 Client ID**:
   - Application type: **Desktop app**
   - Name: `Happy Hour Finder`
9. Click **Download JSON**
10. Save the file as `config/credentials.json`

On first run, a browser window will open for you to authorize the app. After that, a `token.json` is cached so you won't need to re-authorize.

## 3. Configure Friends List

Edit `config/friends.json` with your friends' names and phone numbers (include country code):

```json
{
  "group_name": "Happy Hour Crew",
  "friends": [
    {"name": "Alice", "phone": "+12125551234"},
    {"name": "Bob", "phone": "+12125555678"},
    {"name": "Charlie", "phone": "+12125559012"}
  ]
}
```

## 4. Configure Settings

Edit `config/settings.json` to customize:

- `search_query`: What to search on Google Maps (default: "happy hour bars and restaurants near me")
- `search_radius_miles`: How far to search
- `max_results`: Max number of places to scan
- `preferred_days`: Default days for calendar events
- `preferred_time_start` / `preferred_time_end`: Default happy hour window
- `calendar_name`: Name of the Google Calendar to create

## 5. Run the App

```bash
python src/main.py
```

### Menu Options:

1. **Scan** - Opens Chrome, searches Google Maps, scrapes place details
2. **Browse** - View previously scanned happy hours
3. **Calendar** - Pick venues/days/times and add to Google Calendar
4. **WhatsApp** - Create a group and send happy hour details
5. **View Events** - See upcoming calendar events
6. **Settings** - View current configuration
7. **Full Flow** - Run scan, calendar, and WhatsApp in sequence

## 6. WhatsApp Setup

- On first use, WhatsApp Web will open and show a QR code
- Scan it with your phone (WhatsApp > Settings > Linked Devices > Link a Device)
- The session is saved, so you only need to scan once

## Notes

- The Google Maps scanner opens a visible Chrome window (not headless) for reliability
- Phone numbers in `friends.json` must match your WhatsApp contacts
- Happy hour data is saved in `data/` so you can re-browse without re-scanning
