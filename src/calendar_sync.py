"""
Google Calendar API integration for adding happy hour events.
"""

import json
import os
from datetime import datetime, timedelta
from pathlib import Path

from google.auth.transport.requests import Request
from google.oauth2.credentials import Credentials
from google_auth_oauthlib.flow import InstalledAppFlow
from googleapiclient.discovery import build


CONFIG_DIR = Path(__file__).parent.parent / "config"
DATA_DIR = Path(__file__).parent.parent / "data"
SCOPES = ["https://www.googleapis.com/auth/calendar"]

DAY_TO_RRULE = {
    "Monday": "MO", "Tuesday": "TU", "Wednesday": "WE",
    "Thursday": "TH", "Friday": "FR", "Saturday": "SA", "Sunday": "SU",
}

DAY_TO_ISO = {
    "Monday": 0, "Tuesday": 1, "Wednesday": 2,
    "Thursday": 3, "Friday": 4, "Saturday": 5, "Sunday": 6,
}


def get_calendar_service():
    """Authenticate and return a Google Calendar service object."""
    creds = None
    token_file = CONFIG_DIR / "token.json"
    creds_file = CONFIG_DIR / "credentials.json"

    if token_file.exists():
        creds = Credentials.from_authorized_user_file(str(token_file), SCOPES)

    if not creds or not creds.valid:
        if creds and creds.expired and creds.refresh_token:
            creds.refresh(Request())
        else:
            if not creds_file.exists():
                print("\n[!] credentials.json not found in config/")
                print("    Follow the setup guide to download it from Google Cloud Console.")
                print(f"    Expected location: {creds_file}")
                return None

            flow = InstalledAppFlow.from_client_secrets_file(str(creds_file), SCOPES)
            creds = flow.run_local_server(port=0)

        with open(token_file, "w") as f:
            f.write(creds.to_json())

    return build("calendar", "v3", credentials=creds)


def get_or_create_calendar(service, calendar_name="Happy Hour"):
    """Get or create a dedicated calendar for happy hour events."""
    calendars = service.calendarList().list().execute()
    for cal in calendars.get("items", []):
        if cal["summary"] == calendar_name:
            return cal["id"]

    new_cal = service.calendars().insert(body={
        "summary": calendar_name,
        "description": "Happy hour events at nearby bars and restaurants",
        "timeZone": _get_local_timezone(),
    }).execute()

    print(f"Created calendar: {calendar_name}")
    return new_cal["id"]


def _get_local_timezone():
    """Get the local timezone string."""
    try:
        import tzlocal
        return str(tzlocal.get_localzone())
    except ImportError:
        # Fallback: use system offset
        offset = datetime.now().astimezone().strftime("%z")
        return f"Etc/GMT{'+' if offset[0] == '-' else '-'}{int(offset[1:3])}"


def create_happy_hour_event(service, calendar_id, place, happy_hour, day, timezone=None):
    """Create a recurring weekly calendar event for a happy hour."""
    if timezone is None:
        timezone = _get_local_timezone()

    name = place["name"]
    address = place.get("address", "")
    specials = happy_hour.get("specials", [])
    raw_text = happy_hour.get("raw_text", "")

    start_time = happy_hour.get("start_time", "16:00")
    end_time = happy_hour.get("end_time", "19:00")

    if not start_time:
        start_time = "16:00"
    if not end_time:
        end_time = "19:00"

    # Build description
    desc_parts = [f"Happy Hour at {name}"]
    if raw_text:
        desc_parts.append(f"\nDetails: {raw_text}")
    if specials:
        desc_parts.append("\nSpecials:")
        for s in specials:
            desc_parts.append(f"  - {s}")
    if place.get("phone"):
        desc_parts.append(f"\nPhone: {place['phone']}")
    if place.get("website"):
        desc_parts.append(f"Website: {place['website']}")
    if place.get("maps_url"):
        desc_parts.append(f"Maps: {place['maps_url']}")

    description = "\n".join(desc_parts)

    # Calculate the next occurrence of the selected day
    today = datetime.now()
    target_weekday = DAY_TO_ISO[day]
    days_ahead = (target_weekday - today.weekday()) % 7
    if days_ahead == 0 and today.hour >= int(end_time.split(":")[0]):
        days_ahead = 7
    next_date = today + timedelta(days=days_ahead)
    date_str = next_date.strftime("%Y-%m-%d")

    rrule_day = DAY_TO_RRULE[day]

    label = happy_hour.get("label", "Happy Hour")
    event = {
        "summary": f"🍻 {name} - {label}",
        "location": address,
        "description": description,
        "start": {
            "dateTime": f"{date_str}T{start_time}:00",
            "timeZone": timezone,
        },
        "end": {
            "dateTime": f"{date_str}T{end_time}:00",
            "timeZone": timezone,
        },
        "recurrence": [
            f"RRULE:FREQ=WEEKLY;BYDAY={rrule_day}"
        ],
        "reminders": {
            "useDefault": False,
            "overrides": [
                {"method": "popup", "minutes": 60},
            ],
        },
        "colorId": "6",  # Tangerine
    }

    created = service.events().insert(calendarId=calendar_id, body=event).execute()
    return created


def add_happy_hours_to_calendar(selections):
    """
    Add selected happy hours to Google Calendar.

    selections: list of dicts with keys:
        - place: the place dict
        - happy_hour: the happy hour dict
        - days: list of day names
    """
    service = get_calendar_service()
    if not service:
        return False

    settings_file = CONFIG_DIR / "settings.json"
    with open(settings_file, encoding="utf-8") as f:
        settings = json.load(f)

    calendar_name = settings.get("calendar_name", "Happy Hour")
    calendar_id = get_or_create_calendar(service, calendar_name)
    timezone = _get_local_timezone()

    created_events = []
    for sel in selections:
        place = sel["place"]
        happy_hour = sel["happy_hour"]
        days = sel["days"]

        for day in days:
            try:
                event = create_happy_hour_event(
                    service, calendar_id, place, happy_hour, day, timezone
                )
                print(f"  [+] Created: {event['summary']} on {day}s")
                created_events.append(event)
            except Exception as e:
                print(f"  [!] Failed to create event for {place['name']} on {day}: {e}")

    print(f"\nAdded {len(created_events)} events to '{calendar_name}' calendar.")
    return created_events


def list_existing_events(calendar_name="Happy Hour"):
    """List existing happy hour events."""
    service = get_calendar_service()
    if not service:
        return []

    calendar_id = get_or_create_calendar(service, calendar_name)
    now = datetime.utcnow().isoformat() + "Z"

    events = service.events().list(
        calendarId=calendar_id,
        timeMin=now,
        maxResults=50,
        singleEvents=True,
        orderBy="startTime",
    ).execute()

    return events.get("items", [])


def delete_all_happy_hour_events(calendar_name="Happy Hour", delete_calendar=False):
    """Delete every event from the Happy Hour calendar.

    If delete_calendar=True, the entire calendar is removed instead of
    iterating events (much faster and cleaner).
    """
    service = get_calendar_service()
    if not service:
        return 0

    # Find the calendar
    calendars = service.calendarList().list().execute()
    calendar_id = None
    for cal in calendars.get("items", []):
        if cal["summary"] == calendar_name:
            calendar_id = cal["id"]
            break

    if not calendar_id:
        print(f"No calendar named '{calendar_name}' found. Nothing to delete.")
        return 0

    if delete_calendar:
        try:
            service.calendars().delete(calendarId=calendar_id).execute()
            print(f"[+] Deleted calendar '{calendar_name}' and all its events.")
            return -1  # sentinel meaning "whole calendar removed"
        except Exception as e:
            print(f"[!] Could not delete the calendar itself ({e}).")
            print("    Falling back to deleting all events one by one...")
            # fall through to the per-event deletion below

    # Otherwise, iterate and delete all events (past + future, including recurring)
    deleted = 0
    page_token = None
    while True:
        events_result = service.events().list(
            calendarId=calendar_id,
            maxResults=2500,
            singleEvents=False,  # get recurring event masters, not each instance
            pageToken=page_token,
        ).execute()

        events = events_result.get("items", [])
        for event in events:
            try:
                service.events().delete(
                    calendarId=calendar_id, eventId=event["id"]
                ).execute()
                deleted += 1
                print(f"  [-] Deleted: {event.get('summary', event['id'])}")
            except Exception as e:
                print(f"  [!] Failed to delete {event.get('summary')}: {e}")

        page_token = events_result.get("nextPageToken")
        if not page_token:
            break

    print(f"\n[+] Deleted {deleted} events from '{calendar_name}'.")
    return deleted


if __name__ == "__main__":
    import sys

    if len(sys.argv) > 1 and sys.argv[1] == "delete-all":
        # Delete every event from the Happy Hour calendar
        confirm = input(
            "Delete ALL events from the 'Happy Hour' calendar? (yes/no): "
        ).strip().lower()
        if confirm == "yes":
            delete_all_happy_hour_events()
        else:
            print("Aborted.")
    elif len(sys.argv) > 1 and sys.argv[1] == "delete-calendar":
        # Nuke the entire calendar
        confirm = input(
            "Delete the entire 'Happy Hour' CALENDAR (not just events)? (yes/no): "
        ).strip().lower()
        if confirm == "yes":
            delete_all_happy_hour_events(delete_calendar=True)
        else:
            print("Aborted.")
    else:
        service = get_calendar_service()
        if service:
            print("Google Calendar authentication successful!")
            events = list_existing_events()
            print(f"Found {len(events)} upcoming happy hour events.")
