"""
Headless Google Maps scraper — server-side version.

Differences from the original CLI scanner (src/scanner.py):
  - Headless Chrome by default (no visible window).
  - Returns places as a list of dicts; never writes JSON.
  - Captures latitude/longitude parsed from the place URL.
  - Conservative timeouts so a stuck place doesn't hang the scan.
"""

import re
import time
from typing import Optional

from selenium import webdriver
from selenium.webdriver.chrome.service import Service
from selenium.webdriver.chrome.options import Options
from selenium.webdriver.common.by import By
from selenium.webdriver.support.ui import WebDriverWait
from selenium.webdriver.support import expected_conditions as EC
from webdriver_manager.chrome import ChromeDriverManager

from app.scanner.geo import parse_latlng_from_maps_url


def get_driver(headless: bool = True) -> webdriver.Chrome:
    options = Options()
    if headless:
        options.add_argument("--headless=new")
    options.add_argument("--disable-gpu")
    options.add_argument("--no-sandbox")
    options.add_argument("--disable-dev-shm-usage")
    options.add_argument("--window-size=1920,1080")
    options.add_argument("--lang=en-US")
    # Avoid Chrome's "first run" UI prompts in headless mode
    options.add_argument("--no-first-run")
    options.add_argument("--no-default-browser-check")
    service = Service(ChromeDriverManager().install())
    return webdriver.Chrome(service=service, options=options)


def scan_google_maps(
    query: str = "happy hour bars and restaurants near me",
    max_results: int = 20,
    headless: bool = True,
) -> list[dict]:
    """Search Google Maps and return up to `max_results` places."""
    driver = get_driver(headless=headless)
    places: list[dict] = []

    try:
        url = f"https://www.google.com/maps/search/{query.replace(' ', '+')}"
        driver.get(url)
        time.sleep(4)

        try:
            feed = WebDriverWait(driver, 10).until(
                EC.presence_of_element_located((By.CSS_SELECTOR, 'div[role="feed"]'))
            )
        except Exception:
            feed = driver.find_element(By.CSS_SELECTOR, 'div[role="main"]')

        # Scroll until we have enough cards or stop loading
        last_count = 0
        scroll_attempts = 0
        while scroll_attempts < 15:
            driver.execute_script(
                "arguments[0].scrollTop = arguments[0].scrollHeight", feed
            )
            time.sleep(2)
            scroll_attempts += 1
            cards = driver.find_elements(By.CSS_SELECTOR, 'a[href*="/maps/place/"]')
            if len(cards) >= max_results or len(cards) == last_count:
                break
            last_count = len(cards)

        # Process cards by INDEX, re-fetching the card list each iteration.
        # The DOM is re-rendered every time we click back from a place
        # panel, which invalidates any element references we hold across
        # iterations (Selenium "stale element reference" error).
        initial_count = len(driver.find_elements(By.CSS_SELECTOR, 'a[href*="/maps/place/"]'))
        target = min(initial_count, max_results)
        for i in range(target):
            try:
                # Re-fetch cards on every iteration to avoid stale references
                cards = driver.find_elements(By.CSS_SELECTOR, 'a[href*="/maps/place/"]')
                if i >= len(cards):
                    print(f"  [!] Card list shrank, only {len(cards)} cards remain. Stopping.")
                    break
                card = cards[i]
                place = _extract_place_from_card(driver, card)
                if place:
                    places.append(place)
                    print(f"  [{i+1}/{target}] {place['name']}")
            except Exception as e:
                short_err = str(e).split("\n")[0][:120]
                print(f"  [!] Error extracting place {i+1}: {short_err}")
                # If we hit a fatal error (panel completely broken), give up
                # rather than keep failing on every subsequent index.
                continue

    finally:
        driver.quit()

    print(f"\n[scanner] Found {len(places)} places.")
    return places


def scan_place_by_maps_url(maps_url: str, headless: bool = True) -> Optional[dict]:
    """
    Scrape a single venue's details from a Google Maps place URL.

    Returns a dict with the same shape as scan_google_maps() entries, or
    None if the URL can't be parsed. Used by the manual-seed CLI when
    Google Maps search misses a venue you already know about.
    """
    driver = get_driver(headless=headless)
    try:
        driver.get(maps_url)
        time.sleep(4)
        return _extract_details_from_panel(driver, maps_url)
    finally:
        driver.quit()


def _extract_details_from_panel(driver: webdriver.Chrome, maps_url: str) -> Optional[dict]:
    """
    Pull venue details from the currently-open Google Maps place panel.
    Shared between search-result extraction and direct-URL seeding.
    """
    place: dict = {
        "name": "",
        "maps_url": maps_url,
        "address": "",
        "latitude": None,
        "longitude": None,
        "rating": None,
        "phone": "",
        "website": "",
    }

    # Name from the panel header
    try:
        h1 = driver.find_elements(By.CSS_SELECTOR, "h1.DUwDvf")
        if h1:
            place["name"] = h1[0].text.strip()
    except Exception:
        pass

    # Lat/lng from the resolved URL
    try:
        coords = parse_latlng_from_maps_url(driver.current_url) or parse_latlng_from_maps_url(maps_url)
        if coords:
            place["latitude"], place["longitude"] = coords
            place["maps_url"] = driver.current_url
    except Exception:
        pass

    # Address
    try:
        addr_els = driver.find_elements(
            By.CSS_SELECTOR, 'button[data-item-id="address"] div.fontBodyMedium'
        )
        if addr_els:
            place["address"] = addr_els[0].text.strip()
    except Exception:
        pass

    # Rating (numeric)
    try:
        rating_els = driver.find_elements(By.CSS_SELECTOR, "span.ceNzKf")
        if rating_els:
            label = rating_els[0].get_attribute("aria-label") or rating_els[0].text or ""
            m = re.search(r"(\d+(?:\.\d+)?)", label)
            if m:
                place["rating"] = float(m.group(1))
    except Exception:
        pass

    # Phone
    try:
        phone_els = driver.find_elements(
            By.CSS_SELECTOR, 'button[data-item-id*="phone"] div.fontBodyMedium'
        )
        if phone_els:
            place["phone"] = phone_els[0].text.strip()
    except Exception:
        pass

    # Website
    try:
        website_els = driver.find_elements(
            By.CSS_SELECTOR, 'a[data-item-id="authority"] div.fontBodyMedium'
        )
        if website_els:
            place["website"] = website_els[0].text.strip()
        else:
            website_links = driver.find_elements(
                By.CSS_SELECTOR, 'a[data-item-id="authority"]'
            )
            if website_links:
                place["website"] = website_links[0].get_attribute("href") or ""
    except Exception:
        pass

    if not place["name"]:
        return None
    return place


def _extract_place_from_card(driver: webdriver.Chrome, card) -> Optional[dict]:
    """Click a place card and extract its details from the side panel."""
    name = (card.get_attribute("aria-label") or "").strip()
    href = card.get_attribute("href") or ""

    if not name:
        return None

    driver.execute_script("arguments[0].click();", card)
    time.sleep(2)

    place: dict = {
        "name": name,
        "maps_url": href,
        "address": "",
        "latitude": None,
        "longitude": None,
        "rating": None,
        "phone": "",
        "website": "",
    }

    # Lat/Lng from the place URL (Google updates the address bar after click)
    try:
        current_url = driver.current_url
        coords = parse_latlng_from_maps_url(current_url)
        if not coords:
            coords = parse_latlng_from_maps_url(href)
        if coords:
            place["latitude"], place["longitude"] = coords
            # Update maps_url to the canonical, fully-resolved one
            place["maps_url"] = current_url
    except Exception:
        pass

    # Address
    try:
        addr_els = driver.find_elements(
            By.CSS_SELECTOR, 'button[data-item-id="address"] div.fontBodyMedium'
        )
        if addr_els:
            place["address"] = addr_els[0].text.strip()
    except Exception:
        pass

    # Rating (numeric)
    try:
        rating_els = driver.find_elements(By.CSS_SELECTOR, "span.ceNzKf")
        if rating_els:
            label = rating_els[0].get_attribute("aria-label") or rating_els[0].text or ""
            m = re.search(r"(\d+(?:\.\d+)?)", label)
            if m:
                place["rating"] = float(m.group(1))
    except Exception:
        pass

    # Phone
    try:
        phone_els = driver.find_elements(
            By.CSS_SELECTOR, 'button[data-item-id*="phone"] div.fontBodyMedium'
        )
        if phone_els:
            place["phone"] = phone_els[0].text.strip()
    except Exception:
        pass

    # Website
    try:
        website_els = driver.find_elements(
            By.CSS_SELECTOR, 'a[data-item-id="authority"] div.fontBodyMedium'
        )
        if website_els:
            place["website"] = website_els[0].text.strip()
        else:
            website_links = driver.find_elements(
                By.CSS_SELECTOR, 'a[data-item-id="authority"]'
            )
            if website_links:
                place["website"] = website_links[0].get_attribute("href") or ""
    except Exception:
        pass

    # Go back to results. Retry once if the back button is stale, fall back
    # to driver.back() if the second attempt also fails.
    for attempt in range(2):
        try:
            back_btn = driver.find_element(By.CSS_SELECTOR, 'button[aria-label="Back"]')
            back_btn.click()
            time.sleep(1.5)
            break
        except Exception:
            if attempt == 1:
                try:
                    driver.back()
                    time.sleep(1.5)
                except Exception:
                    pass

    return place
