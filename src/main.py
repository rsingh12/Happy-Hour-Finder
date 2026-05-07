"""
Happy Hour Finder & Social Planner - CLI Orchestrator
Scans nearby bars/restaurants for happy hours, adds to Google Calendar,
and creates WhatsApp groups with friends.
"""

import json
import sys
from pathlib import Path

import inquirer

# Add parent to path for imports
sys.path.insert(0, str(Path(__file__).parent))

from scanner import scan_google_maps
from happy_hour_parser import parse_all_places
from calendar_sync import (
    add_happy_hours_to_calendar,
    list_existing_events,
    get_calendar_service,
    delete_all_happy_hour_events,
)
from whatsapp_bot import create_group_and_invite

CONFIG_DIR = Path(__file__).parent.parent / "config"
DATA_DIR = Path(__file__).parent.parent / "data"

DAY_NAMES = ["Monday", "Tuesday", "Wednesday", "Thursday", "Friday", "Saturday", "Sunday"]


def load_settings():
    settings_file = CONFIG_DIR / "settings.json"
    with open(settings_file, encoding="utf-8") as f:
        return json.load(f)


def load_happy_hours():
    hh_file = DATA_DIR / "happy_hours.json"
    if not hh_file.exists():
        return None
    with open(hh_file, encoding="utf-8") as f:
        return json.load(f)


def display_happy_hours(places):
    """Display discovered happy hours in a readable format."""
    print("\n" + "=" * 60)
    print("  DISCOVERED HAPPY HOURS")
    print("=" * 60)

    for i, place in enumerate(places):
        hh_list = place.get("happy_hours", [])
        print(f"\n  [+] {i+1}. {place['name']}")
        if place.get("address"):
            print(f"      Address: {place['address']}")
        if place.get("rating"):
            print(f"      Rating: {place['rating']}")
        if place.get("website"):
            print(f"      Website: {place['website']}")

        for hh in hh_list:
            label = hh.get("label", "Happy Hour")
            days = hh.get("days", [])
            start = hh.get("start_time") or "?"
            end = hh.get("end_time") or "?"
            src = hh.get("source", "")

            print(f"      • {label} ({src})")
            if days:
                print(f"          Days: {', '.join(days)}")
            if start != "?" or end != "?":
                print(f"          Time: {start} - {end}")
            specials = hh.get("specials", [])
            if specials:
                print("          Specials:")
                for s in specials[:5]:
                    print(f"            - {s}")

    print("\n" + "=" * 60)


def menu_scan():
    """Scan for nearby happy hours."""
    settings = load_settings()
    print("\n[*] Scanning Google Maps for nearby happy hours...")
    print(f"    Query: {settings['search_query']}")
    print(f"    Max results: {settings['max_results']}")

    places = scan_google_maps(
        query=settings["search_query"],
        max_results=settings["max_results"],
    )

    if not places:
        print("\n[!] No places found. Try adjusting your search query in settings.json")
        return

    print("\n[*] Parsing happy hour details from websites...")
    enriched = parse_all_places(places)
    display_happy_hours(enriched)


def menu_browse():
    """Browse previously discovered happy hours."""
    places = load_happy_hours()
    if not places:
        print("\n[!] No happy hour data found. Run a scan first (option 1).")
        return
    display_happy_hours(places)


def menu_add_to_calendar():
    """Pick venues (days/times all come from scraping, no prompts)."""
    places = load_happy_hours()
    if not places:
        print("\n[!] No happy hour data found. Run a scan first (option 1).")
        return

    # Every place in happy_hours.json already has scraped info (parser filters them)
    # Build venue choices — one checkbox per (venue, happy_hour_entry) pair so user
    # can independently select e.g. weekday vs late-night specials at the same bar.
    venue_choices = []
    venue_map = []
    for p in places:
        for hh in p.get("happy_hours", []):
            days = hh.get("days") or []
            start = hh.get("start_time") or "?"
            end = hh.get("end_time") or "?"
            label = hh.get("label", "Happy Hour")

            day_str = ",".join(d[:3] for d in days) if days else "days unknown"
            time_str = f"{start}-{end}" if start != "?" or end != "?" else "time unknown"

            line = f"{p['name']} — {label} [{day_str} {time_str}]"
            venue_choices.append((line, len(venue_map)))
            venue_map.append({"place": p, "happy_hour": hh})

    if not venue_choices:
        print("\n[!] No venues with happy hour info. Re-scan or check websites.")
        return

    answers = inquirer.prompt([
        inquirer.Checkbox(
            "picks",
            message="Select happy hours to add to your calendar (Space to toggle, Enter to confirm)",
            choices=venue_choices,
        ),
    ])
    if not answers or not answers["picks"]:
        print("Nothing selected.")
        return

    selections = []
    for idx in answers["picks"]:
        item = venue_map[idx]
        hh = item["happy_hour"]
        days = hh.get("days") or []

        # Skip entries where we have no days (we need a day to create a recurring event)
        if not days:
            print(f"  [!] Skipping {item['place']['name']} — no scraped days of week")
            continue
        if not hh.get("start_time") or not hh.get("end_time"):
            print(f"  [!] Skipping {item['place']['name']} — no scraped time range")
            continue

        selections.append({
            "place": item["place"],
            "happy_hour": hh,
            "days": days,
        })

    if not selections:
        print("\nNothing to add.")
        return

    # Summary
    print("\n" + "=" * 50)
    print("  EVENTS TO ADD")
    print("=" * 50)
    for sel in selections:
        place = sel["place"]
        hh = sel["happy_hour"]
        days = sel["days"]
        print(f"  {place['name']} ({hh.get('label', 'Happy Hour')})")
        print(f"    Days: {', '.join(days)}")
        print(f"    Time: {hh.get('start_time')} - {hh.get('end_time')}")
        if hh.get("specials"):
            for s in hh["specials"][:3]:
                print(f"      • {s}")
    print("=" * 50)

    confirm = inquirer.prompt([
        inquirer.Confirm("confirm", message="Add these events to Google Calendar?", default=True)
    ])

    if confirm and confirm["confirm"]:
        print("\n[*] Adding events to Google Calendar...")
        events = add_happy_hours_to_calendar(selections)
        if events:
            print("[+] Calendar events created successfully!")

            # Store selections for WhatsApp
            sel_file = DATA_DIR / "last_selections.json"
            # Convert for JSON serialization
            serializable = []
            for s in selections:
                serializable.append({
                    "place": {k: v for k, v in s["place"].items() if k != "happy_hours"},
                    "happy_hour": s["happy_hour"],
                    "days": s["days"],
                })
            with open(sel_file, "w", encoding="utf-8") as f:
                json.dump(serializable, f, indent=2)

        return selections
    return None


def menu_whatsapp():
    """Create WhatsApp group and send happy hour details."""
    # Load last selections
    sel_file = DATA_DIR / "last_selections.json"
    selections = []
    if sel_file.exists():
        with open(sel_file, encoding="utf-8") as f:
            selections = json.load(f)

    friends_file = CONFIG_DIR / "friends.json"
    if not friends_file.exists():
        print("\n[!] friends.json not found. Please create it in config/")
        print("    Template:")
        print('    {"group_name": "Happy Hour Crew", "friends": [{"name": "Alice", "phone": "+1234567890"}]}')
        return

    with open(friends_file, encoding="utf-8") as f:
        friends = json.load(f)

    print(f"\n[*] Friends to invite: {len(friends.get('friends', []))}")
    for f_info in friends.get("friends", []):
        print(f"    - {f_info['name']} ({f_info['phone']})")

    group_name_q = inquirer.prompt([
        inquirer.Text(
            "group_name",
            message="WhatsApp group name",
            default=friends.get("group_name", "Happy Hour Crew"),
        )
    ])

    if not group_name_q:
        return

    confirm = inquirer.prompt([
        inquirer.Confirm(
            "confirm",
            message="Create WhatsApp group and send happy hour details?",
            default=True,
        )
    ])

    if confirm and confirm["confirm"]:
        create_group_and_invite(selections, group_name_q["group_name"])


def menu_delete_events():
    """Delete all events from the Happy Hour calendar (testing helper)."""
    print("\n[!] This will delete ALL events from the 'Happy Hour' calendar.")
    print("    The calendar itself will stay, so you can re-add events later.")

    confirm = inquirer.prompt([
        inquirer.Confirm(
            "confirm",
            message="Really delete all happy hour calendar events?",
            default=False,
        )
    ])
    if not confirm or not confirm["confirm"]:
        print("Aborted.")
        return

    settings = load_settings()
    calendar_name = settings.get("calendar_name", "Happy Hour")
    deleted = delete_all_happy_hour_events(calendar_name=calendar_name)
    if deleted and deleted > 0:
        print(f"\n[+] Done. {deleted} events deleted.")
    elif deleted == 0:
        print("\n[i] Calendar was already empty.")


def menu_view_events():
    """View existing happy hour calendar events."""
    print("\n[*] Fetching upcoming happy hour events...")
    events = list_existing_events()

    if not events:
        print("No upcoming happy hour events found.")
        return

    print(f"\nFound {len(events)} upcoming events:\n")
    for event in events:
        start = event["start"].get("dateTime", event["start"].get("date"))
        summary = event.get("summary", "Untitled")
        location = event.get("location", "")
        print(f"  {start[:16]}  {summary}")
        if location:
            print(f"               {location}")


def menu_settings():
    """View and edit settings."""
    settings = load_settings()
    print("\nCurrent settings:")
    print(json.dumps(settings, indent=2))
    print(f"\nEdit: config/settings.json")
    print(f"Friends: config/friends.json")


def main():
    print("""
    ╔══════════════════════════════════════╗
    ║   🍻 Happy Hour Finder & Planner   ║
    ║   Cost-effective socializing!       ║
    ╚══════════════════════════════════════╝
    """)

    while True:
        menu = inquirer.prompt([
            inquirer.List(
                "action",
                message="What would you like to do?",
                choices=[
                    ("1. Scan for nearby happy hours", "scan"),
                    ("2. Browse discovered deals", "browse"),
                    ("3. Add happy hours to Google Calendar", "calendar"),
                    ("4. Create WhatsApp group & invite friends", "whatsapp"),
                    ("5. View upcoming calendar events", "events"),
                    ("6. View settings", "settings"),
                    ("7. Full flow (scan -> calendar -> WhatsApp)", "full"),
                    ("8. Delete ALL happy hour calendar events (testing)", "delete"),
                    ("9. Exit", "exit"),
                ],
            )
        ])

        if not menu:
            break

        action = menu["action"]

        if action == "scan":
            menu_scan()
        elif action == "browse":
            menu_browse()
        elif action == "calendar":
            menu_add_to_calendar()
        elif action == "whatsapp":
            menu_whatsapp()
        elif action == "events":
            menu_view_events()
        elif action == "settings":
            menu_settings()
        elif action == "delete":
            menu_delete_events()
        elif action == "full":
            print("\n[*] Running full flow: Scan -> Calendar -> WhatsApp\n")
            menu_scan()
            selections = menu_add_to_calendar()
            if selections:
                menu_whatsapp()
        elif action == "exit":
            print("\nCheers! 🍻")
            break

        print()


if __name__ == "__main__":
    main()
