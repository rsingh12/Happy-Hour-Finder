"""initial schema

Revision ID: 0001
Revises:
Create Date: 2026-04-29 00:00:00.000000

Creates all v1 tables: users, venues, happy_hours, outings,
outing_members, friends, device_tokens, email_verification_codes.
"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects.postgresql import ARRAY, UUID


revision: str = "0001"
down_revision: Union[str, None] = None
branch_labels = None
depends_on = None


def upgrade() -> None:
    # users
    op.create_table(
        "users",
        sa.Column("id", UUID(as_uuid=True), primary_key=True),
        sa.Column("email", sa.String(255), nullable=False, unique=True),
        sa.Column("password_hash", sa.String(255), nullable=False),
        sa.Column("display_name", sa.String(120)),
        sa.Column("phone", sa.String(32)),
        sa.Column("email_verified", sa.Boolean, nullable=False, server_default=sa.false()),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()),
    )
    op.create_index("ix_users_email", "users", ["email"], unique=True)
    op.create_index("ix_users_phone", "users", ["phone"])

    # venues
    op.create_table(
        "venues",
        sa.Column("id", UUID(as_uuid=True), primary_key=True),
        sa.Column("name", sa.String(255), nullable=False),
        sa.Column("address", sa.Text),
        sa.Column("latitude", sa.Float),
        sa.Column("longitude", sa.Float),
        sa.Column("google_maps_url", sa.Text),
        sa.Column("website", sa.Text),
        sa.Column("phone", sa.String(64)),
        sa.Column("rating", sa.Float),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()),
    )
    op.create_index("ix_venues_latitude", "venues", ["latitude"])
    op.create_index("ix_venues_longitude", "venues", ["longitude"])

    # happy_hours
    op.create_table(
        "happy_hours",
        sa.Column("id", UUID(as_uuid=True), primary_key=True),
        sa.Column(
            "venue_id",
            UUID(as_uuid=True),
            sa.ForeignKey("venues.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column("label", sa.String(64), nullable=False, server_default="Happy Hour"),
        sa.Column("days", ARRAY(sa.String(16)), nullable=False, server_default="{}"),
        sa.Column("start_time", sa.Time),
        sa.Column("end_time", sa.Time),
        sa.Column("specials", ARRAY(sa.Text), nullable=False, server_default="{}"),
        sa.Column("source", sa.String(32)),
        sa.Column("confidence", sa.String(16)),
        sa.Column("scanned_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()),
    )
    op.create_index("ix_happy_hours_venue_id", "happy_hours", ["venue_id"])

    # outings
    op.create_table(
        "outings",
        sa.Column("id", UUID(as_uuid=True), primary_key=True),
        sa.Column(
            "organizer_id",
            UUID(as_uuid=True),
            sa.ForeignKey("users.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column("venue_id", UUID(as_uuid=True), sa.ForeignKey("venues.id"), nullable=False),
        sa.Column("happy_hour_id", UUID(as_uuid=True), sa.ForeignKey("happy_hours.id")),
        sa.Column("outing_date", sa.Date, nullable=False),
        sa.Column("start_time", sa.Time, nullable=False),
        sa.Column("end_time", sa.Time, nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()),
    )
    op.create_index("ix_outings_organizer_id", "outings", ["organizer_id"])
    op.create_index("ix_outings_outing_date", "outings", ["outing_date"])

    # outing_members
    op.create_table(
        "outing_members",
        sa.Column(
            "outing_id",
            UUID(as_uuid=True),
            sa.ForeignKey("outings.id", ondelete="CASCADE"),
            primary_key=True,
        ),
        sa.Column(
            "user_id",
            UUID(as_uuid=True),
            sa.ForeignKey("users.id", ondelete="CASCADE"),
            primary_key=True,
        ),
        sa.Column("status", sa.String(16), nullable=False, server_default="invited"),
        sa.Column("invited_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()),
        sa.Column("responded_at", sa.DateTime(timezone=True)),
    )

    # friends
    op.create_table(
        "friends",
        sa.Column("id", UUID(as_uuid=True), primary_key=True),
        sa.Column(
            "user_id",
            UUID(as_uuid=True),
            sa.ForeignKey("users.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column(
            "friend_id",
            UUID(as_uuid=True),
            sa.ForeignKey("users.id", ondelete="CASCADE"),
        ),
        sa.Column("pending_phone", sa.String(32)),
        sa.Column("pending_email", sa.String(255)),
        sa.Column("display_name", sa.String(120)),
        sa.Column("status", sa.String(16), nullable=False, server_default="pending"),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()),
        sa.UniqueConstraint("user_id", "friend_id", name="uq_friends_user_friend"),
    )
    op.create_index("ix_friends_user_id", "friends", ["user_id"])
    op.create_index("ix_friends_friend_id", "friends", ["friend_id"])

    # device_tokens
    op.create_table(
        "device_tokens",
        sa.Column("id", UUID(as_uuid=True), primary_key=True),
        sa.Column(
            "user_id",
            UUID(as_uuid=True),
            sa.ForeignKey("users.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column("apns_token", sa.Text, nullable=False),
        sa.Column("device_id", sa.String(128), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()),
        sa.UniqueConstraint("user_id", "device_id", name="uq_device_tokens_user_device"),
    )
    op.create_index("ix_device_tokens_user_id", "device_tokens", ["user_id"])

    # email_verification_codes
    op.create_table(
        "email_verification_codes",
        sa.Column("id", UUID(as_uuid=True), primary_key=True),
        sa.Column(
            "user_id",
            UUID(as_uuid=True),
            sa.ForeignKey("users.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column("code", sa.String(6), nullable=False),
        sa.Column("expires_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("used", sa.Boolean, nullable=False, server_default=sa.false()),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()),
    )
    op.create_index(
        "ix_email_verification_codes_user_id",
        "email_verification_codes",
        ["user_id"],
    )


def downgrade() -> None:
    op.drop_table("email_verification_codes")
    op.drop_table("device_tokens")
    op.drop_table("friends")
    op.drop_table("outing_members")
    op.drop_table("outings")
    op.drop_table("happy_hours")
    op.drop_table("venues")
    op.drop_table("users")
