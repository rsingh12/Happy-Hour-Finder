"""
WhatsApp Web automation for creating happy hour groups and sending invites.
"""

import json
import time
from pathlib import Path

from selenium import webdriver
from selenium.webdriver.chrome.service import Service
from selenium.webdriver.chrome.options import Options
from selenium.webdriver.common.by import By
from selenium.webdriver.common.keys import Keys
from selenium.webdriver.support.ui import WebDriverWait
from selenium.webdriver.support import expected_conditions as EC
from webdriver_manager.chrome import ChromeDriverManager


CONFIG_DIR = Path(__file__).parent.parent / "config"
WHATSAPP_DATA_DIR = Path(__file__).parent.parent / "data" / "whatsapp_profile"


def get_whatsapp_driver():
    """Create a Chrome driver with persistent profile for WhatsApp Web."""
    options = Options()
    # Use a persistent profile so QR code only needs scanning once
    WHATSAPP_DATA_DIR.mkdir(parents=True, exist_ok=True)
    options.add_argument(f"--user-data-dir={WHATSAPP_DATA_DIR}")
    options.add_argument("--no-sandbox")
    options.add_argument("--disable-gpu")
    options.add_argument("--window-size=1400,900")
    service = Service(ChromeDriverManager().install())
    return webdriver.Chrome(service=service, options=options)


def wait_for_whatsapp_login(driver, timeout=120):
    """Wait for user to scan QR code and WhatsApp Web to load."""
    driver.get("https://web.whatsapp.com")
    print("\n[*] WhatsApp Web is loading...")
    print("    If this is your first time, scan the QR code with your phone.")
    print("    (WhatsApp > Settings > Linked Devices > Link a Device)\n")

    try:
        # Wait for the main chat pane to appear (indicates successful login)
        WebDriverWait(driver, timeout).until(
            EC.presence_of_element_located((By.CSS_SELECTOR, '#pane-side'))
        )
        print("[+] WhatsApp Web logged in successfully!")
        time.sleep(2)
        return True
    except Exception:
        print("[!] Timed out waiting for WhatsApp login.")
        return False


def create_group(driver, group_name, phone_numbers):
    """Create a new WhatsApp group and add members by phone number."""
    print(f"\n[*] Creating group: {group_name}")

    # Click the "New chat" button
    try:
        new_chat_btn = WebDriverWait(driver, 10).until(
            EC.element_to_be_clickable((By.CSS_SELECTOR, 'div[title="New chat"]'))
        )
        new_chat_btn.click()
        time.sleep(1)
    except Exception:
        # Try alternative selector
        try:
            new_chat_btn = driver.find_element(
                By.CSS_SELECTOR, 'span[data-icon="new-chat-outline"]'
            )
            new_chat_btn.click()
            time.sleep(1)
        except Exception as e:
            print(f"[!] Could not find 'New chat' button: {e}")
            return False

    # Click "New group"
    try:
        new_group_btn = WebDriverWait(driver, 10).until(
            EC.element_to_be_clickable((By.CSS_SELECTOR, 'div[title="New group"]'))
        )
        new_group_btn.click()
        time.sleep(1)
    except Exception:
        # Try text-based search
        try:
            elements = driver.find_elements(By.XPATH, "//*[contains(text(), 'New group')]")
            for el in elements:
                if el.is_displayed():
                    el.click()
                    break
            time.sleep(1)
        except Exception as e:
            print(f"[!] Could not find 'New group' option: {e}")
            return False

    # Add contacts by phone number
    added = []
    search_box = None
    try:
        search_box = WebDriverWait(driver, 10).until(
            EC.presence_of_element_located(
                (By.CSS_SELECTOR, 'input[title="Type a contact name"]')
            )
        )
    except Exception:
        try:
            search_box = driver.find_element(
                By.CSS_SELECTOR, 'div[contenteditable="true"][data-tab]'
            )
        except Exception as e:
            print(f"[!] Could not find contact search box: {e}")
            return False

    for phone in phone_numbers:
        try:
            search_box.clear()
            time.sleep(0.5)

            # Type the phone number
            search_box.send_keys(phone)
            time.sleep(2)

            # Look for a matching contact
            contacts = driver.find_elements(
                By.CSS_SELECTOR, 'div[role="listitem"] span[title]'
            )

            if contacts:
                contacts[0].click()
                added.append(phone)
                print(f"  [+] Added: {phone}")
                time.sleep(1)
            else:
                # Try clicking on any visible contact result
                results = driver.find_elements(
                    By.CSS_SELECTOR, 'div[role="listitem"]'
                )
                if results:
                    results[0].click()
                    added.append(phone)
                    print(f"  [+] Added: {phone}")
                    time.sleep(1)
                else:
                    print(f"  [!] Contact not found: {phone}")

            # Clear search
            search_box.clear()
            time.sleep(0.5)
        except Exception as e:
            print(f"  [!] Error adding {phone}: {e}")
            continue

    if not added:
        print("[!] No contacts were added. Group creation aborted.")
        return False

    # Click the forward/next arrow to proceed
    try:
        next_btn = WebDriverWait(driver, 10).until(
            EC.element_to_be_clickable(
                (By.CSS_SELECTOR, 'span[data-icon="arrow-forward"]')
            )
        )
        next_btn.click()
        time.sleep(2)
    except Exception:
        try:
            next_btn = driver.find_element(
                By.CSS_SELECTOR, 'div[role="button"] span[data-icon="arrow-forward"]'
            )
            next_btn.click()
            time.sleep(2)
        except Exception as e:
            print(f"[!] Could not proceed to group naming: {e}")
            return False

    # Set group name
    try:
        name_input = WebDriverWait(driver, 10).until(
            EC.presence_of_element_located(
                (By.CSS_SELECTOR, 'div[contenteditable="true"][data-tab]')
            )
        )
        name_input.send_keys(group_name)
        time.sleep(1)
    except Exception as e:
        print(f"[!] Could not set group name: {e}")
        return False

    # Click the create/check button
    try:
        create_btn = WebDriverWait(driver, 10).until(
            EC.element_to_be_clickable(
                (By.CSS_SELECTOR, 'span[data-icon="checkmark-medium"]')
            )
        )
        create_btn.click()
        time.sleep(3)
    except Exception:
        try:
            create_btn = driver.find_element(
                By.CSS_SELECTOR, 'div[role="button"] span[data-icon="checkmark-large"]'
            )
            create_btn.click()
            time.sleep(3)
        except Exception as e:
            print(f"[!] Could not create group: {e}")
            return False

    print(f"\n[+] Group '{group_name}' created with {len(added)} members!")
    return True


def send_message(driver, message):
    """Send a message in the currently open chat."""
    try:
        msg_box = WebDriverWait(driver, 10).until(
            EC.presence_of_element_located(
                (By.CSS_SELECTOR, 'div[contenteditable="true"][data-tab="10"]')
            )
        )

        # Handle multi-line messages (Shift+Enter for newlines)
        lines = message.split("\n")
        for i, line in enumerate(lines):
            msg_box.send_keys(line)
            if i < len(lines) - 1:
                msg_box.send_keys(Keys.SHIFT + Keys.ENTER)

        time.sleep(0.5)
        msg_box.send_keys(Keys.ENTER)
        time.sleep(2)
        print("[+] Message sent!")
        return True
    except Exception as e:
        print(f"[!] Could not send message: {e}")
        return False


def format_happy_hour_message(selections):
    """Format happy hour selections into a WhatsApp message."""
    lines = ["🍻 *Happy Hour Picks!* 🍻", ""]

    for sel in selections:
        place = sel["place"]
        hh = sel["happy_hour"]
        days = sel["days"]

        lines.append(f"📍 *{place['name']}*")
        if place.get("address"):
            lines.append(f"   {place['address']}")

        day_str = ", ".join(days)
        start = hh.get("start_time", "TBD")
        end = hh.get("end_time", "TBD")
        lines.append(f"   🕐 {day_str}: {start} - {end}")

        specials = hh.get("specials", [])
        if specials:
            lines.append("   🎉 Specials:")
            for s in specials:
                lines.append(f"      • {s}")

        if place.get("maps_url"):
            lines.append(f"   🗺️ {place['maps_url']}")

        lines.append("")

    lines.append("Let's go! Who's in? 🙋‍♂️🙋‍♀️")
    return "\n".join(lines)


def create_group_and_invite(selections, group_name=None):
    """Full flow: create WhatsApp group and send happy hour details."""
    # Load friends config
    friends_file = CONFIG_DIR / "friends.json"
    if not friends_file.exists():
        print("[!] friends.json not found in config/")
        return False

    with open(friends_file, encoding="utf-8") as f:
        friends_config = json.load(f)

    if group_name is None:
        group_name = friends_config.get("group_name", "Happy Hour Crew")

    friends = friends_config.get("friends", [])
    phone_numbers = [f["phone"] for f in friends if f.get("phone")]

    if not phone_numbers:
        print("[!] No phone numbers found in friends.json")
        return False

    driver = get_whatsapp_driver()

    try:
        if not wait_for_whatsapp_login(driver):
            return False

        if not create_group(driver, group_name, phone_numbers):
            return False

        # Send happy hour details
        if selections:
            message = format_happy_hour_message(selections)
            send_message(driver, message)

        print("\n[+] WhatsApp group setup complete!")
        print("    The browser will stay open so you can verify.")
        print("    Press Enter to close...")
        input()

    finally:
        driver.quit()

    return True


if __name__ == "__main__":
    # Test with dummy data
    test_selections = [{
        "place": {
            "name": "Test Bar",
            "address": "123 Main St",
            "maps_url": "https://maps.google.com/test",
        },
        "happy_hour": {
            "start_time": "16:00",
            "end_time": "19:00",
            "specials": ["$5 margaritas", "Half price wings"],
        },
        "days": ["Friday"],
    }]
    create_group_and_invite(test_selections)
