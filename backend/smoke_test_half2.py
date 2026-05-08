"""
End-to-end smoke test for Week 2 Half 2.

Walks through:
  1. Register two users (A organizer, B invitee)
  2. Pull verification codes straight from Postgres (no email)
  3. Verify both
  4. Login as A, add B as a friend (resolves immediately since B has account)
  5. Pick a venue from the existing scanned data
  6. Create an outing
  7. Invite B to the outing
  8. Login as B, accept the invite
  9. Get the outing detail (should show both members with status)
 10. Test the suggestion endpoint

Prints a clear PASS/FAIL summary at the end.
"""

import sys
import time
import uuid
from datetime import date, timedelta

import httpx
from sqlalchemy import text

from app.database import SessionLocal


BASE = "http://localhost:8000"
TS = int(time.time())  # unique suffix for emails so we don't collide with DB
A_EMAIL = f"smoke_a_{TS}@example.com"
B_EMAIL = f"smoke_b_{TS}@example.com"
A_PASSWORD = "smoke-test-pw-A1!"
B_PASSWORD = "smoke-test-pw-B1!"


def step(label: str) -> None:
    print(f"\n--- {label} ---")


def fetch_verification_code(user_email: str) -> str:
    """Pull the latest unused verification code for a user from the DB."""
    db = SessionLocal()
    try:
        row = db.execute(text("""
            SELECT evc.code
            FROM email_verification_codes evc
            JOIN users u ON u.id = evc.user_id
            WHERE u.email = :email AND evc.used = false
            ORDER BY evc.created_at DESC
            LIMIT 1
        """), {"email": user_email.lower()}).fetchone()
        if not row:
            raise RuntimeError(f"No verification code found for {user_email}")
        return row.code
    finally:
        db.close()


def assert_ok(r: httpx.Response, label: str) -> dict:
    if r.status_code >= 400:
        print(f"   [FAIL] {label}: {r.status_code} {r.text}")
        raise SystemExit(1)
    print(f"   [ok]   {label}: {r.status_code}")
    return r.json()


def main() -> int:
    with httpx.Client(timeout=15.0) as client:
        # 1. REGISTER A
        step("Register user A")
        r = client.post(f"{BASE}/auth/register", json={
            "email": A_EMAIL,
            "password": A_PASSWORD,
            "display_name": "Alice Smoke",
        })
        a_data = assert_ok(r, f"register A ({A_EMAIL})")

        # 2. REGISTER B
        step("Register user B")
        r = client.post(f"{BASE}/auth/register", json={
            "email": B_EMAIL,
            "password": B_PASSWORD,
            "display_name": "Bob Smoke",
        })
        b_data = assert_ok(r, f"register B ({B_EMAIL})")

        # 3. VERIFY both via DB code lookup
        step("Verify user A")
        a_code = fetch_verification_code(A_EMAIL)
        print(f"   pulled A's code from DB: {a_code}")
        r = client.post(f"{BASE}/auth/verify", json={"email": A_EMAIL, "code": a_code})
        assert_ok(r, "verify A")

        step("Verify user B")
        b_code = fetch_verification_code(B_EMAIL)
        print(f"   pulled B's code from DB: {b_code}")
        r = client.post(f"{BASE}/auth/verify", json={"email": B_EMAIL, "code": b_code})
        assert_ok(r, "verify B")

        # 4. LOGIN as A
        step("Login as A")
        r = client.post(f"{BASE}/auth/login", json={"email": A_EMAIL, "password": A_PASSWORD})
        a_token = assert_ok(r, "login A")["access_token"]
        a_headers = {"Authorization": f"Bearer {a_token}"}
        a_user_id = a_data["user_id"]

        # 5. A ADDS B AS FRIEND
        step("A adds B as a friend (by email)")
        r = client.post(f"{BASE}/friends", json={"email": B_EMAIL}, headers=a_headers)
        friend = assert_ok(r, "add friend")
        print(f"   friendship status: {friend['status']}, friend_user_id: {friend.get('friend_user_id')}")
        assert friend["status"] == "active", "Friendship should be active immediately since B exists"

        # 6. LIST FRIENDS for A
        step("List A's friends")
        r = client.get(f"{BASE}/friends", headers=a_headers)
        flist = assert_ok(r, "list friends")
        print(f"   A has {len(flist)} friend(s)")

        # 7. PICK A VENUE
        step("Find a venue with a happy hour")
        r = client.get(
            f"{BASE}/venues?lat=38.7521&lng=-121.296&radius_miles=25&limit=10",
            headers=a_headers,
        )
        venues = assert_ok(r, "list venues")
        if not venues:
            print("   ❌ No venues in DB. Run the scanner first.")
            return 1
        # Pick a venue that has at least one happy hour with full times
        target = None
        for v in venues:
            for hh in v.get("happy_hours", []):
                if hh.get("days") and hh.get("start_time") and hh.get("end_time"):
                    target = (v, hh)
                    break
            if target:
                break
        if not target:
            print("   ❌ No venue with complete HH found.")
            return 1
        venue, hh = target
        print(f"   Picked venue: {venue['name']}")
        print(f"   Picked HH: {hh['label']} {hh['start_time']}-{hh['end_time']}")

        # 8. CREATE OUTING
        step("A creates an outing")
        # Find the next occurrence of one of this HH's days
        from datetime import datetime
        DAYS = ["Monday", "Tuesday", "Wednesday", "Thursday", "Friday", "Saturday", "Sunday"]
        today = date.today()
        outing_date = None
        for offset in range(0, 14):
            d = today + timedelta(days=offset)
            if DAYS[d.weekday()] in hh["days"]:
                outing_date = d
                break
        outing_date = outing_date or (today + timedelta(days=1))

        r = client.post(f"{BASE}/outings", json={
            "venue_id": venue["id"],
            "happy_hour_id": hh["id"],
            "outing_date": outing_date.isoformat(),
            "start_time": hh["start_time"],
            "end_time": hh["end_time"],
        }, headers=a_headers)
        outing = assert_ok(r, "create outing")
        outing_id = outing["id"]
        print(f"   outing_id: {outing_id}")
        print(f"   organizer auto-joined? {any(m['user_id'] == a_user_id and m['status'] == 'accepted' for m in outing['members'])}")

        # 9. A INVITES B
        step("A invites B to the outing")
        b_user_id = b_data["user_id"]
        r = client.post(f"{BASE}/outings/{outing_id}/invite", json={
            "user_ids": [b_user_id],
        }, headers=a_headers)
        outing_after = assert_ok(r, "invite B")
        b_member = next((m for m in outing_after["members"] if m["user_id"] == b_user_id), None)
        print(f"   B's status: {b_member['status'] if b_member else 'NOT FOUND'}")

        # 10. LOGIN AS B
        step("Login as B")
        r = client.post(f"{BASE}/auth/login", json={"email": B_EMAIL, "password": B_PASSWORD})
        b_token = assert_ok(r, "login B")["access_token"]
        b_headers = {"Authorization": f"Bearer {b_token}"}

        # 11. B RESPONDS ACCEPTED
        step("B accepts the invite")
        r = client.post(f"{BASE}/outings/{outing_id}/respond", json={
            "response": "accepted",
        }, headers=b_headers)
        outing_final = assert_ok(r, "B accepts")
        b_member_final = next((m for m in outing_final["members"] if m["user_id"] == b_user_id), None)
        print(f"   B's status: {b_member_final['status']}")
        assert b_member_final["status"] == "accepted"

        # 12. GET OUTING DETAIL (as B, who is now a member)
        step("Get outing detail as B (verifies authorization)")
        r = client.get(f"{BASE}/outings/{outing_id}", headers=b_headers)
        detail = assert_ok(r, "get outing detail")
        print(f"   members: {[(m['display_name'], m['status']) for m in detail['members']]}")

        # 13. SUGGESTION
        step("Suggestion endpoint")
        r = client.get(
            f"{BASE}/outings/suggest?lat=38.7521&lng=-121.296&radius_miles=25",
            headers=a_headers,
        )
        suggestion = assert_ok(r, "get suggestion")
        if suggestion:
            print(f"   suggested: {suggestion['day_of_week']} {suggestion['start_time']}-{suggestion['end_time']}")
            print(f"   {suggestion['venue_count']} venues, next date: {suggestion['next_date']}")
        else:
            print("   (no suggestion — DB likely empty)")

    # Final summary
    print("\n" + "=" * 60)
    print("ALL STEPS PASSED")
    print("=" * 60)
    print(f"User A: {A_EMAIL}")
    print(f"User B: {B_EMAIL}")
    print(f"Outing: {outing_id}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
