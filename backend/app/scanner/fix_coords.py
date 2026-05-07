"""
One-shot script to re-parse coordinates for all venues using the
fixed parse_latlng_from_maps_url logic.

Run once after fixing the regex bug:
    python -m app.scanner.fix_coords
"""

from app.database import SessionLocal
from app.models import Venue
from app.scanner.geo import parse_latlng_from_maps_url


def main() -> None:
    db = SessionLocal()
    try:
        venues = db.query(Venue).all()
        updated = 0
        unchanged = 0
        no_url = 0

        for v in venues:
            if not v.google_maps_url:
                no_url += 1
                continue

            coords = parse_latlng_from_maps_url(v.google_maps_url)
            if not coords:
                continue

            new_lat, new_lng = coords
            if (v.latitude, v.longitude) == (new_lat, new_lng):
                unchanged += 1
                continue

            print(
                f"  {v.name}: "
                f"({v.latitude:.6f}, {v.longitude:.6f}) -> ({new_lat:.6f}, {new_lng:.6f})"
            )
            v.latitude = new_lat
            v.longitude = new_lng
            updated += 1

        db.commit()
        print(
            f"\nDone. Updated: {updated}, unchanged: {unchanged}, "
            f"no URL: {no_url}, total: {len(venues)}"
        )
    finally:
        db.close()


if __name__ == "__main__":
    main()
