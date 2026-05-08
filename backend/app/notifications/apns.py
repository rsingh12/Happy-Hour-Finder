"""
APNs (Apple Push Notification service) client.

Behavior:
  - When APNS_KEY_PATH/APNS_KEY_ID/APNS_TEAM_ID/APNS_BUNDLE_ID are all
    set, real pushes are sent via aioapns.
  - Otherwise, send_push() logs to stdout and returns True. This lets
    the rest of the system work end-to-end before an Apple Developer
    account exists.

Real APNs setup (when ready):
  1. Sign in to Apple Developer Console
  2. Certificates, Identifiers & Profiles → Keys → +
  3. Enable "Apple Push Notifications service (APNs)"
  4. Download the .p8 file (you can only download it once)
  5. Set in .env:
       APNS_KEY_PATH=/path/to/AuthKey_XXXX.p8
       APNS_KEY_ID=XXXX
       APNS_TEAM_ID=YYYYYYYYYY
       APNS_BUNDLE_ID=app.happyhour.ios   # match your iOS app's bundle id
       APNS_USE_SANDBOX=true              # 'true' for TestFlight, 'false' for App Store
"""

from __future__ import annotations

import asyncio
import logging
from pathlib import Path
from typing import Optional

from app.config import settings


logger = logging.getLogger(__name__)


def is_configured() -> bool:
    return bool(
        settings.apns_key_path
        and settings.apns_key_id
        and settings.apns_team_id
        and settings.apns_bundle_id
        and Path(settings.apns_key_path).exists()
    )


async def send_push_async(
    *,
    apns_token: str,
    title: str,
    body: str,
    payload_extras: Optional[dict] = None,
) -> bool:
    """Send one push to one device. Returns True on success / dev-stub."""
    if not is_configured():
        # Dev-mode: log it and pretend it succeeded
        print(
            f"\n[DEV PUSH] -> {apns_token[:12]}…"
            f"\n[DEV PUSH] Title: {title}"
            f"\n[DEV PUSH] Body:  {body}"
            f"\n[DEV PUSH] Extras: {payload_extras or {}}\n"
        )
        return True

    try:
        from aioapns import APNs, NotificationRequest, PushType
    except ImportError:
        logger.error("aioapns not installed. pip install aioapns")
        return False

    try:
        apns_client = APNs(
            key=settings.apns_key_path,
            key_id=settings.apns_key_id,
            team_id=settings.apns_team_id,
            topic=settings.apns_bundle_id,
            use_sandbox=settings.apns_use_sandbox,
        )

        message = {
            "aps": {
                "alert": {"title": title, "body": body},
                "sound": "default",
            }
        }
        if payload_extras:
            message.update(payload_extras)

        request = NotificationRequest(
            device_token=apns_token,
            message=message,
            push_type=PushType.ALERT,
        )

        result = await apns_client.send_notification(request)
        return bool(result.is_successful)
    except Exception as e:
        logger.exception("APNs push failed: %s", e)
        return False


def send_push(
    *,
    apns_token: str,
    title: str,
    body: str,
    payload_extras: Optional[dict] = None,
) -> bool:
    """Synchronous wrapper around send_push_async for use in non-async contexts."""
    try:
        return asyncio.run(send_push_async(
            apns_token=apns_token,
            title=title,
            body=body,
            payload_extras=payload_extras,
        ))
    except RuntimeError:
        # Already inside an event loop — schedule and wait
        loop = asyncio.get_event_loop()
        return loop.run_until_complete(send_push_async(
            apns_token=apns_token,
            title=title,
            body=body,
            payload_extras=payload_extras,
        ))
