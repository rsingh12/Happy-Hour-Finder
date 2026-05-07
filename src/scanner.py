"""
Google Maps scraper to find nearby bars and restaurants with happy hours.
"""

import json
import time
import re
from pathlib import Path
from selenium import webdriver
from selenium.webdriver.chrome.service import Service
from selenium.webdriver.chrome.options import Options
from selenium.webdriver.common.by import By
from selenium.webdriver.common.keys import Keys
from selenium.webdriver.support.ui import WebDriverWait
from selenium.webdriver.support import expected_conditions as EC
from webdriver_manager.chrome import ChromeDriverManager


DATA_DIR = Path(__file__).parent.parent / "data"


def get_driver(headless=False):
    options = Options()
    if headless:
        options.add_argument("--headless=new")
    options.add_argument("--disable-gpu")
    options.add_argument("--no-sandbox")
    options.add_argument("--window-size=1920,1080")
    options.add_argument("--lang=en-US")
    service = Service(ChromeDriverManager().install())
    return webdriver.Chrome(service=service, options=options)


def scan_google_maps(query="happy hour bars and restaurants near me", max_results=20):
    """Search Google Maps and extract place details."""
    driver = get_driver(headless=False)
    places = []

    try:
        url = f"https://www.google.com/maps/search/{query.replace(' ', '+')}"
        driver.get(url)
        time.sleep(4)

        # Scroll the results panel to load more places
        results_selector = 'div[role="feed"]'
        try:
            feed = WebDriverWait(driver, 10).until(
                EC.presence_of_element_located((By.CSS_SELECTOR, results_selector))
            )
        except Exception:
            # Fallback: try the results list
            feed = driver.find_element(By.CSS_SELECTOR, 'div[role="main"]')

        last_count = 0
        scroll_attempts = 0
        while len(places) < max_results and scroll_attempts < 15:
            driver.execute_script(
                "arguments[0].scrollTop = arguments[0].scrollHeight", feed
            )
            time.sleep(2)
            scroll_attempts += 1

            # Extract place cards
            cards = driver.find_elements(By.CSS_SELECTOR, 'a[href*="/maps/place/"]')
            if len(cards) == last_count:
                break
            last_count = len(cards)

        # Now click each card and extract details
        cards = driver.find_elements(By.CSS_SELECTOR, 'a[href*="/maps/place/"]')
        for i, card in enumerate(cards[:max_results]):
            try:
                place = _extract_place_from_card(driver, card)
                if place:
                    places.append(place)
                    print(f"  [{i+1}/{min(len(cards), max_results)}] {place['name']}")
            except Exception as e:
                print(f"  [!] Error extracting place {i+1}: {e}")
                continue

    finally:
        driver.quit()

    # Save results
    DATA_DIR.mkdir(exist_ok=True)
    output_file = DATA_DIR / "scanned_places.json"
    with open(output_file, "w", encoding="utf-8") as f:
        json.dump(places, f, indent=2, ensure_ascii=False)

    print(f"\nFound {len(places)} places. Saved to {output_file}")
    return places


def _scrape_happy_hour_reviews(driver, max_reviews=30):
    """
    Click the Reviews tab, search for 'happy hour', and return matching review texts.
    Uses Google Maps' built-in review search to filter server-side.
    """
    reviews = []

    # Click the Reviews tab
    reviews_tab = None
    try:
        tabs = driver.find_elements(By.CSS_SELECTOR, 'button[role="tab"]')
        for tab in tabs:
            label = (tab.get_attribute("aria-label") or "") + " " + (tab.text or "")
            if "review" in label.lower():
                reviews_tab = tab
                break
    except Exception:
        pass

    if not reviews_tab:
        return reviews

    try:
        driver.execute_script("arguments[0].click();", reviews_tab)
        time.sleep(2)
    except Exception:
        return reviews

    # Try to use the "Search reviews" input to filter for "happy hour"
    search_filled = False
    try:
        search_inputs = driver.find_elements(
            By.CSS_SELECTOR, 'input[aria-label*="Search reviews" i]'
        )
        if not search_inputs:
            search_inputs = driver.find_elements(
                By.CSS_SELECTOR, 'input[placeholder*="Search reviews" i]'
            )
        if search_inputs:
            box = search_inputs[0]
            box.clear()
            box.send_keys("happy hour")
            time.sleep(2)
            search_filled = True
    except Exception:
        pass

    # Scroll the reviews panel to load more
    try:
        scrollable = None
        panels = driver.find_elements(
            By.CSS_SELECTOR, 'div[role="main"] div[tabindex="-1"]'
        )
        for p in panels:
            if p.size.get("height", 0) > 200:
                scrollable = p
                break
        if scrollable:
            for _ in range(4):
                driver.execute_script(
                    "arguments[0].scrollTop = arguments[0].scrollHeight", scrollable
                )
                time.sleep(1)
    except Exception:
        pass

    # Expand any "More" buttons to reveal full review text
    try:
        more_buttons = driver.find_elements(
            By.CSS_SELECTOR, 'button[aria-label*="See more" i], button.w8nwRe'
        )
        for btn in more_buttons[:20]:
            try:
                driver.execute_script("arguments[0].click();", btn)
            except Exception:
                continue
        time.sleep(1)
    except Exception:
        pass

    # Extract review text bodies
    try:
        review_els = driver.find_elements(By.CSS_SELECTOR, "span.wiI7pd")
        if not review_els:
            review_els = driver.find_elements(By.CSS_SELECTOR, 'div[data-review-id] span')

        for el in review_els[:max_reviews]:
            text = (el.text or "").strip()
            if not text:
                continue
            # If we didn't use server-side filtering, only keep reviews mentioning happy hour
            if not search_filled and "happy hour" not in text.lower():
                continue
            reviews.append(text)
    except Exception:
        pass

    return reviews


def _extract_place_from_card(driver, card):
    """Click a place card and extract its details from the side panel."""
    name = card.get_attribute("aria-label") or ""
    href = card.get_attribute("href") or ""

    if not name:
        return None

    # Click to open details panel
    driver.execute_script("arguments[0].click();", card)
    time.sleep(2)

    place = {
        "name": name.strip(),
        "maps_url": href,
        "address": "",
        "rating": "",
        "price_level": "",
        "phone": "",
        "website": "",
        "hours": [],
        "happy_hour_hint": "",
        "reviews": [],
    }

    try:
        # Address
        addr_els = driver.find_elements(
            By.CSS_SELECTOR, 'button[data-item-id="address"] div.fontBodyMedium'
        )
        if addr_els:
            place["address"] = addr_els[0].text.strip()

        # Rating
        rating_els = driver.find_elements(By.CSS_SELECTOR, "span.ceNzKf")
        if rating_els:
            place["rating"] = rating_els[0].get_attribute("aria-label") or rating_els[0].text

        # Phone
        phone_els = driver.find_elements(
            By.CSS_SELECTOR, 'button[data-item-id*="phone"] div.fontBodyMedium'
        )
        if phone_els:
            place["phone"] = phone_els[0].text.strip()

        # Website
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

        # Operating hours
        try:
            hours_button = driver.find_elements(
                By.CSS_SELECTOR, 'button[data-item-id="oh"]'
            )
            if hours_button:
                driver.execute_script("arguments[0].click();", hours_button[0])
                time.sleep(1)
                hour_rows = driver.find_elements(
                    By.CSS_SELECTOR, "table.eK4R0e tr"
                )
                for row in hour_rows:
                    cells = row.find_elements(By.TAG_NAME, "td")
                    if len(cells) >= 2:
                        day = cells[0].text.strip()
                        time_text = cells[1].text.strip()
                        place["hours"].append({"day": day, "hours": time_text})
        except Exception:
            pass

        # Check page text for happy hour mentions
        try:
            page_text = driver.find_element(By.CSS_SELECTOR, 'div[role="main"]').text
            hh_patterns = [
                r"happy\s*hour[s]?",
                r"drink\s*special[s]?",
                r"half[\s-]*price",
                r"\$\d+\s*(beer|wine|cocktail|margarita|draft)",
                r"2[\s-]*for[\s-]*1",
            ]
            hints = []
            for pattern in hh_patterns:
                matches = re.findall(pattern, page_text, re.IGNORECASE)
                if matches:
                    hints.extend(matches)
            if hints:
                place["happy_hour_hint"] = "; ".join(set(hints))
        except Exception:
            pass

    except Exception:
        pass

    # Scrape reviews that mention happy hour
    try:
        place["reviews"] = _scrape_happy_hour_reviews(driver)
        if place["reviews"]:
            print(f"      (found {len(place['reviews'])} happy-hour reviews)")
    except Exception as e:
        pass

    # Go back to results
    try:
        back_btn = driver.find_element(By.CSS_SELECTOR, 'button[aria-label="Back"]')
        back_btn.click()
        time.sleep(1)
    except Exception:
        driver.back()
        time.sleep(1)

    return place


def fetch_reviews_for_existing_places():
    """Re-open each previously scanned place in Google Maps and pull its
    happy-hour reviews. Useful when reviews were not captured in the first scan.
    """
    input_file = DATA_DIR / "scanned_places.json"
    if not input_file.exists():
        print("No scanned_places.json found. Run a full scan first.")
        return []

    with open(input_file, encoding="utf-8") as f:
        places = json.load(f)

    driver = get_driver(headless=False)
    try:
        for i, place in enumerate(places):
            if place.get("reviews"):
                continue  # already have reviews
            url = place.get("maps_url")
            if not url:
                continue
            print(f"[{i+1}/{len(places)}] Fetching reviews for: {place['name']}")
            try:
                driver.get(url)
                time.sleep(3)
                place["reviews"] = _scrape_happy_hour_reviews(driver)
                print(f"    Got {len(place['reviews'])} reviews")
            except Exception as e:
                print(f"    Error: {e}")
                place["reviews"] = []
    finally:
        driver.quit()

    with open(input_file, "w", encoding="utf-8") as f:
        json.dump(places, f, indent=2, ensure_ascii=False)

    print(f"\nUpdated {input_file}")
    return places


if __name__ == "__main__":
    import sys
    if len(sys.argv) > 1 and sys.argv[1] == "reviews":
        fetch_reviews_for_existing_places()
    else:
        settings_file = Path(__file__).parent.parent / "config" / "settings.json"
        with open(settings_file, encoding="utf-8") as f:
            settings = json.load(f)
        scan_google_maps(
            query=settings.get("search_query", "happy hour bars near me"),
            max_results=settings.get("max_results", 20),
        )
