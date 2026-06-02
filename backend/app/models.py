"""
SQLAlchemy ORM models for the Happy Hour app.

Schema mirrors the data model in the plan file:
  - users, venues, happy_hours
  - outings, outing_members
  - friends, device_tokens
  - email_verification_codes (helper for auth flow)
"""

from __future__ import annotations

import enum
import uuid
from datetime import datetime, date, time
from typing import Optional

from sqlalchemy import (
    Boolean,
    Date,
    DateTime,
    Enum,
    Float,
    ForeignKey,
    Integer,
    String,
    Text,
    Time,
    UniqueConstraint,
    func,
)
from sqlalchemy.dialects.postgresql import ARRAY, UUID
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.database import Base


class DayOfWeek(str, enum.Enum):
    MONDAY = "Monday"
    TUESDAY = "Tuesday"
    WEDNESDAY = "Wednesday"
    THURSDAY = "Thursday"
    FRIDAY = "Friday"
    SATURDAY = "Saturday"
    SUNDAY = "Sunday"


class ExtractionSource(str, enum.Enum):
    LLM = "llm"
    LLM_VISION = "llm_vision"
    REGEX = "regex"
    MANUAL = "manual"


class Confidence(str, enum.Enum):
    HIGH = "high"
    MEDIUM = "medium"
    LOW = "low"


def _uuid_pk():
    return mapped_column(
        UUID(as_uuid=True),
        primary_key=True,
        default=uuid.uuid4,
    )


def _now():
    return mapped_column(
        DateTime(timezone=True),
        server_default=func.now(),
        nullable=False,
    )


class User(Base):
    __tablename__ = "users"

    id: Mapped[uuid.UUID] = _uuid_pk()
    email: Mapped[str] = mapped_column(String(255), unique=True, nullable=False, index=True)
    password_hash: Mapped[str] = mapped_column(String(255), nullable=False)
    display_name: Mapped[Optional[str]] = mapped_column(String(120))
    phone: Mapped[Optional[str]] = mapped_column(String(32), index=True)
    email_verified: Mapped[bool] = mapped_column(Boolean, default=False, nullable=False)
    created_at: Mapped[datetime] = _now()


class Venue(Base):
    __tablename__ = "venues"

    id: Mapped[uuid.UUID] = _uuid_pk()
    name: Mapped[str] = mapped_column(String(255), nullable=False)
    address: Mapped[Optional[str]] = mapped_column(Text)
    # Use plain lat/lng floats for v1; PostGIS deferred to v2.
    latitude: Mapped[Optional[float]] = mapped_column(Float, index=True)
    longitude: Mapped[Optional[float]] = mapped_column(Float, index=True)
    google_maps_url: Mapped[Optional[str]] = mapped_column(Text)
    google_place_id: Mapped[Optional[str]] = mapped_column(
        String(128), unique=True, index=True
    )
    normalized_name: Mapped[Optional[str]] = mapped_column(String(255), index=True)
    website: Mapped[Optional[str]] = mapped_column(Text)
    phone: Mapped[Optional[str]] = mapped_column(String(64))
    rating: Mapped[Optional[float]] = mapped_column(Float)
    created_at: Mapped[datetime] = _now()

    happy_hours: Mapped[list["HappyHour"]] = relationship(
        back_populates="venue",
        cascade="all, delete-orphan",
    )


class HappyHour(Base):
    __tablename__ = "happy_hours"

    id: Mapped[uuid.UUID] = _uuid_pk()
    venue_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("venues.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )
    label: Mapped[str] = mapped_column(String(64), default="Happy Hour", nullable=False)
    days: Mapped[list[DayOfWeek]] = mapped_column(
        ARRAY(
            Enum(
                DayOfWeek,
                name="day_of_week",
                values_callable=lambda e: [m.value for m in e],
            )
        ),
        default=list,
        nullable=False,
    )
    start_time: Mapped[Optional[time]] = mapped_column(Time)
    end_time: Mapped[Optional[time]] = mapped_column(Time)
    specials: Mapped[list[str]] = mapped_column(ARRAY(Text), default=list, nullable=False)
    source: Mapped[Optional[ExtractionSource]] = mapped_column(
        Enum(
            ExtractionSource,
            name="extraction_source",
            values_callable=lambda e: [m.value for m in e],
        )
    )
    confidence: Mapped[Optional[Confidence]] = mapped_column(
        Enum(
            Confidence,
            name="confidence_level",
            values_callable=lambda e: [m.value for m in e],
        )
    )
    scanned_at: Mapped[datetime] = _now()

    venue: Mapped["Venue"] = relationship(back_populates="happy_hours")


class Outing(Base):
    __tablename__ = "outings"

    id: Mapped[uuid.UUID] = _uuid_pk()
    organizer_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("users.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )
    venue_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("venues.id"),
        nullable=False,
    )
    happy_hour_id: Mapped[Optional[uuid.UUID]] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("happy_hours.id"),
    )
    outing_date: Mapped[date] = mapped_column(Date, nullable=False, index=True)
    start_time: Mapped[time] = mapped_column(Time, nullable=False)
    end_time: Mapped[time] = mapped_column(Time, nullable=False)
    created_at: Mapped[datetime] = _now()

    members: Mapped[list["OutingMember"]] = relationship(
        back_populates="outing",
        cascade="all, delete-orphan",
    )


class OutingMember(Base):
    __tablename__ = "outing_members"

    outing_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("outings.id", ondelete="CASCADE"),
        primary_key=True,
    )
    user_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("users.id", ondelete="CASCADE"),
        primary_key=True,
    )
    status: Mapped[str] = mapped_column(
        String(16), default="invited", nullable=False  # invited / accepted / declined
    )
    invited_at: Mapped[datetime] = _now()
    responded_at: Mapped[Optional[datetime]] = mapped_column(DateTime(timezone=True))

    outing: Mapped["Outing"] = relationship(back_populates="members")


class Friend(Base):
    """
    A user's friendship to another user.

    - When the friend already has an account, friend_id is set.
    - When the friend hasn't signed up yet, friend_id is null and we
      store pending_phone or pending_email. Once they sign up, a
      reconciliation job promotes the row by filling friend_id.
    """
    __tablename__ = "friends"

    id: Mapped[uuid.UUID] = _uuid_pk()
    user_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("users.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )
    friend_id: Mapped[Optional[uuid.UUID]] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("users.id", ondelete="CASCADE"),
        index=True,
    )
    pending_phone: Mapped[Optional[str]] = mapped_column(String(32))
    pending_email: Mapped[Optional[str]] = mapped_column(String(255))
    display_name: Mapped[Optional[str]] = mapped_column(String(120))
    status: Mapped[str] = mapped_column(
        String(16), default="pending", nullable=False  # pending / active
    )
    created_at: Mapped[datetime] = _now()

    __table_args__ = (
        UniqueConstraint("user_id", "friend_id", name="uq_friends_user_friend"),
    )


class DeviceToken(Base):
    __tablename__ = "device_tokens"

    id: Mapped[uuid.UUID] = _uuid_pk()
    user_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("users.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )
    apns_token: Mapped[str] = mapped_column(Text, nullable=False)
    device_id: Mapped[str] = mapped_column(String(128), nullable=False)
    created_at: Mapped[datetime] = _now()

    __table_args__ = (
        UniqueConstraint("user_id", "device_id", name="uq_device_tokens_user_device"),
    )


class EmailVerificationCode(Base):
    """
    6-digit codes sent to a user's email for email verification.
    Codes expire after 15 minutes; only the latest unused code per user
    is valid.
    """
    __tablename__ = "email_verification_codes"

    id: Mapped[uuid.UUID] = _uuid_pk()
    user_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("users.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )
    code: Mapped[str] = mapped_column(String(6), nullable=False)
    expires_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    used: Mapped[bool] = mapped_column(Boolean, default=False, nullable=False)
    created_at: Mapped[datetime] = _now()
