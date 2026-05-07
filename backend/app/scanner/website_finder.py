"""
Website finder — given a venue's name + address, find its public website.

Why this exists:
  Yelp Fusion's free tier doesn't return business websites, only the
  Yelp page URL. After Yelp gives us a list of venues, we still need
  their actual websites to crawl for happy hour info.

Strategy:
  Open Google Maps with a targeted search ("{name} {city}") and grab
  the website from the resulting place panel. Uses one shared Selenium
  session for the whole batch rather than spinning up Chrome per venue.

Speed:
  ~3-5 seconds per lookup. For 60 venues that's 3-5 minutes. Acceptable
  for a weekly background scan, would be slow for on-demand.

When this is replaced:
  Phase 2 of the discovery work will add Google Places API, which DOES
  return websites in its details endpoint. At that point this module
  becomes obsolete.
"""

from __future__ import annotations

import time
from typing import Optional

from selenium.common.exceptions import NoSuchElementException, TimeoutException
from selenium.webdriver.common.by import By
from selenium.webdriver.support import expected_conditions as EC
from selenium.webdriver.support.ui import WebDriverWait

from app.scanner.scanner import get_driver


def find_websites_for_venues(
    venues: list[dict],
    headless: bool = True,
    skip_if_present: bool = True,
) -> list[dict]:
    """
    Mutates `venues` in place, filling in `website` (and `maps_url`)
    fields by searching Google Maps for each one.

    Returns the same list. If a venue's website is already populated,
    it's skipped unless `skip_if_present=False`.
    """
    targets = [v for v in venues if not (skip_if_present and v.get("website"))]
    if not targets:
        return venues

    print(f"[website-finder] Looking up websites for {len(targets)} venues...")

    driver = get_driver(headless=headless)
    try:
        for i, venue in enumerate(targets, start=1):
            name = venue.get("name") or ""
            address = venue.get("address") or ""
            if not name:
                continue
            label = f"[{i}/{len(targets)}] {name}"
            try:
                website, maps_url = _lookup_one(driver, name, address)
                venue["website"] = website
                if maps_url:
                    venue["maps_url"] = maps_url
                if website:
                    print(f"  {label}: {website}")
                else:
                    print(f"  {label}: (no website found)")
            except Exception as e:
                print(f"  {label}: error - {e}")
                continue
    finally:
        driver.quit()

    return venues


def _lookup_one(driver, name: str, address: str) -> tuple[str, str]:
    """Search Google Maps for one venue, return (website_url, maps_url)."""
    query_parts = [name]
    if address:
        # Use the city + state portion of the address to disambiguate
        query_parts.append(address)
    query = " ".join(query_parts)
    encoded = query.replace(" ", "+").replace(",", "%2C")
    url = f"https://www.google.com/maps/search/{encoded}"

    driver.get(url)
    time.sleep(2)

    # Sometimes Google Maps lands directly on a place panel; sometimes on a
    # results list. Either way, pull the website link from the panel.
    website = ""
    maps_url = driver.current_url

    try:
        # The website link in the place panel
        website_els = driver.find_elements(
            By.CSS_SELECTOR, 'a[data-item-id="authority"]'
        )
        if website_els:
            href = website_els[0].get_attribute("href") or ""
            text = website_els[0].text or ""
            # Prefer the href since it's the actual URL
            website = href or text
    except Exception:
        pass

    if not website:
        # Click the first result if we got a list, then look again
        try:
            cards = driver.find_elements(
                By.CSS_SELECTOR, 'a[href*="/maps/place/"]'
            )
            if cards:
                driver.execute_script("arguments[0].click();", cards[0])
                time.sleep(2)
                maps_url = driver.current_url
                website_els = driver.find_elements(
                    By.CSS_SELECTOR, 'a[data-item-id="authority"]'
                )
                if website_els:
                    website = website_els[0].get_attribute("href") or ""
        except Exception:
            pass

    return website, maps_url
