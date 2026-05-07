"""
Database cleanup — runs the data-quality rules that should hold but
sometimes don't, either from old scanner versions or LLM partial outputs.

Run ad-hoc:
    python -m app.scanner.cleanup

Or with --dry-run to preview without deleting:
    python -m app.scanner.cleanup --dry-run

Rules enforced (in order):
  1. Delete happy_hours rows missing start_time OR end_time.
  2. Within each venue, collapse duplicate happy_hours (same days +
     start + end + label) — keep the most recently scanned.
  3. Delete venues that have zero happy_hours after steps 1-2.
  4. Print any remaining venue duplicates by normalized (name, address)
     — these need manual review since merging requires deciding which
     to keep.
"""

from __future__ import annotations

import argparse
import sys

from sqlalchemy import text

from app.database import SessionLocal


def cleanup(dry_run: bool = False) -> dict:
    db = SessionLocal()
    try:
        # 1a. Count HH rows missing times
        count_incomplete = db.execute(
            text("""
                SELECT COUNT(*) FROM happy_hours
                WHERE start_time IS NULL OR end_time IS NULL
            """)
        ).scalar()

        if not dry_run:
            db.execute(
                text("""
                    DELETE FROM happy_hours
                    WHERE start_time IS NULL OR end_time IS NULL
                """)
            )
            print(f"[1a] Deleted {count_incomplete} happy_hours rows missing times.")
        else:
            print(f"[1a] Would delete {count_incomplete} happy_hours rows missing times.")

        # 1b. Reject entries starting before 11am (AM/PM parse errors)
        count_early = db.execute(
            text("""
                SELECT COUNT(*) FROM happy_hours
                WHERE start_time < TIME '11:00:00'
            """)
        ).scalar()
        if not dry_run:
            db.execute(
                text("""
                    DELETE FROM happy_hours
                    WHERE start_time < TIME '11:00:00'
                """)
            )
            print(f"[1b] Deleted {count_early} happy_hours starting before 11am (AM/PM errors).")
        else:
            print(f"[1b] Would delete {count_early} happy_hours starting before 11am.")

        # 2. Dedupe HH within each venue. Keep most recent by scanned_at.
        # Postgres-specific: uses ROW_NUMBER() to identify duplicates.
        dup_count = db.execute(
            text("""
                WITH ranked AS (
                    SELECT id,
                           ROW_NUMBER() OVER (
                               PARTITION BY venue_id, days, start_time, end_time,
                                            COALESCE(label, '')
                               ORDER BY scanned_at DESC, id
                           ) AS rn
                    FROM happy_hours
                )
                SELECT COUNT(*) FROM ranked WHERE rn > 1
            """)
        ).scalar()

        if not dry_run:
            db.execute(
                text("""
                    DELETE FROM happy_hours
                    WHERE id IN (
                        SELECT id FROM (
                            SELECT id,
                                   ROW_NUMBER() OVER (
                                       PARTITION BY venue_id, days, start_time, end_time,
                                                    COALESCE(label, '')
                                       ORDER BY scanned_at DESC, id
                                   ) AS rn
                            FROM happy_hours
                        ) dups
                        WHERE rn > 1
                    )
                """)
            )
            print(f"[2] Deleted {dup_count} duplicate happy_hours within venues.")
        else:
            print(f"[2] Would delete {dup_count} duplicate happy_hours within venues.")

        # 2b. Merge same-time entries within a venue by union of days.
        # Catches cases like "Mon,Sat 3-6pm" + "Thu,Fri 3-6pm" + "Fri,Sat 3-6pm"
        # which should be one row "Mon,Thu,Fri,Sat 3-6pm".
        merge_groups = db.execute(
            text("""
                SELECT venue_id, start_time, end_time, COALESCE(label, '') AS label,
                       array_agg(id ORDER BY scanned_at DESC) AS ids
                FROM happy_hours
                WHERE start_time IS NOT NULL AND end_time IS NOT NULL
                GROUP BY venue_id, start_time, end_time, COALESCE(label, '')
                HAVING COUNT(*) > 1
            """)
        ).fetchall()

        merged_count = 0
        for row in merge_groups:
            ids = row.ids
            if len(ids) < 2:
                continue
            keeper_id = ids[0]  # most recently scanned
            losers = ids[1:]

            # Collect all days and specials from the losers + keeper
            siblings = db.execute(
                text("""
                    SELECT id, days, specials FROM happy_hours
                    WHERE id = ANY(:ids)
                """),
                {"ids": ids},
            ).fetchall()

            day_union = set()
            specials_union: list[str] = []
            seen_specials = set()
            for s in siblings:
                for d in (s.days or []):
                    day_union.add(d)
                for sp in (s.specials or []):
                    if sp and sp not in seen_specials:
                        specials_union.append(sp)
                        seen_specials.add(sp)

            # Sort days in calendar order
            day_order = {
                "Monday": 0, "Tuesday": 1, "Wednesday": 2, "Thursday": 3,
                "Friday": 4, "Saturday": 5, "Sunday": 6,
            }
            sorted_days = sorted(day_union, key=lambda d: day_order.get(d, 99))

            if not dry_run:
                db.execute(
                    text("""
                        UPDATE happy_hours
                        SET days = :days, specials = :specials
                        WHERE id = :id
                    """),
                    {"days": sorted_days, "specials": specials_union, "id": keeper_id},
                )
                db.execute(
                    text("DELETE FROM happy_hours WHERE id = ANY(:ids)"),
                    {"ids": losers},
                )
            merged_count += len(losers)

        if not dry_run:
            print(f"[2b] Merged {merged_count} same-time HH rows by union of days.")
        else:
            print(f"[2b] Would merge {merged_count} same-time HH rows by union of days.")

        # 3. Orphaned venues (no remaining HH)
        orphan_count = db.execute(
            text("""
                SELECT COUNT(*) FROM venues v
                WHERE NOT EXISTS (
                    SELECT 1 FROM happy_hours h WHERE h.venue_id = v.id
                )
            """)
        ).scalar()

        if not dry_run:
            db.execute(
                text("""
                    DELETE FROM venues v
                    WHERE NOT EXISTS (
                        SELECT 1 FROM happy_hours h WHERE h.venue_id = v.id
                    )
                """)
            )
            print(f"[3] Deleted {orphan_count} orphaned venues (no happy hours).")
        else:
            print(f"[3] Would delete {orphan_count} orphaned venues.")

        if not dry_run:
            db.commit()

        # 4. Identify venue duplicates by normalized (name, address)
        # for manual review — merging is non-trivial since the duplicates
        # may have different attached happy_hours.
        suspect_dups = db.execute(
            text("""
                WITH norm AS (
                    SELECT id, name, address,
                           LOWER(TRIM(name)) AS nname,
                           LOWER(TRIM(COALESCE(address, ''))) AS naddr
                    FROM venues
                )
                SELECT nname, naddr, COUNT(*) AS dup_count,
                       array_agg(id) AS ids,
                       array_agg(name) AS names,
                       array_agg(address) AS addresses
                FROM norm
                GROUP BY nname, naddr
                HAVING COUNT(*) > 1
                ORDER BY dup_count DESC
            """)
        ).fetchall()

        if suspect_dups:
            print(f"\n[4] Found {len(suspect_dups)} suspected venue duplicates "
                  f"(same normalized name+address). Review manually:")
            for row in suspect_dups[:20]:
                print(f"    - {row.names[0]!r} x{row.dup_count} (ids: {row.ids})")
            if len(suspect_dups) > 20:
                print(f"    ...and {len(suspect_dups) - 20} more")
        else:
            print("\n[4] No exact-match venue duplicates found.")

        return {
            "deleted_incomplete_hh": count_incomplete,
            "deleted_early_am_hh": count_early,
            "deleted_duplicate_hh": dup_count,
            "merged_same_time_hh": merged_count,
            "deleted_orphan_venues": orphan_count,
            "suspect_venue_dups": len(suspect_dups),
        }
    finally:
        db.close()


def main() -> int:
    parser = argparse.ArgumentParser(description="Clean up scanner data.")
    parser.add_argument(
        "--dry-run",
        action="store_true",
        help="Show what would be deleted without changing the database.",
    )
    args = parser.parse_args()

    result = cleanup(dry_run=args.dry_run)
    print(f"\nSummary: {result}")
    if args.dry_run:
        print("(--dry-run was set; no changes committed.)")
    return 0


if __name__ == "__main__":
    sys.exit(main())
