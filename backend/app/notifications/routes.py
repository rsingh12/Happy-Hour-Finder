"""
Device registration for push notifications:
  POST /devices/register   — store/update an APNs token for the current device
"""

from __future__ import annotations

import uuid

from fastapi import APIRouter, Depends, status
from pydantic import BaseModel, Field
from sqlalchemy.orm import Session

from app.auth.deps import get_current_verified_user
from app.database import get_db
from app.models import DeviceToken, User


router = APIRouter(prefix="/devices", tags=["devices"])


class RegisterDeviceRequest(BaseModel):
    apns_token: str = Field(min_length=8, max_length=2048)
    device_id: str = Field(
        min_length=1,
        max_length=128,
        description="iOS identifierForVendor — stable per app install",
    )


class DeviceOut(BaseModel):
    id: str
    apns_token: str
    device_id: str


@router.post("/register", response_model=DeviceOut, status_code=status.HTTP_201_CREATED)
def register_device(
    payload: RegisterDeviceRequest,
    user: User = Depends(get_current_verified_user),
    db: Session = Depends(get_db),
):
    """Upsert (user_id, device_id). If the device already exists, refresh its token."""
    existing = (
        db.query(DeviceToken)
        .filter(
            DeviceToken.user_id == user.id,
            DeviceToken.device_id == payload.device_id,
        )
        .one_or_none()
    )

    if existing:
        existing.apns_token = payload.apns_token
        db.commit()
        db.refresh(existing)
        return DeviceOut(
            id=str(existing.id),
            apns_token=existing.apns_token,
            device_id=existing.device_id,
        )

    record = DeviceToken(
        user_id=user.id,
        apns_token=payload.apns_token,
        device_id=payload.device_id,
    )
    db.add(record)
    db.commit()
    db.refresh(record)
    return DeviceOut(
        id=str(record.id),
        apns_token=record.apns_token,
        device_id=record.device_id,
    )
