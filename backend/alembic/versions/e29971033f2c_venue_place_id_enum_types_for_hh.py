"""venue place_id + enum types for happy_hours

Revision ID: e29971033f2c
Revises: 0001
Create Date: 2026-05-21 13:47:55.902912
"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql


revision: str = "e29971033f2c"
down_revision: Union[str, None] = "0001"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


DAY_VALUES = ("Monday", "Tuesday", "Wednesday", "Thursday", "Friday", "Saturday", "Sunday")
SOURCE_VALUES = ("llm", "llm_vision", "regex", "manual")
CONFIDENCE_VALUES = ("high", "medium", "low")


def upgrade() -> None:
    op.add_column("venues", sa.Column("google_place_id", sa.String(length=128), nullable=True))
    op.add_column("venues", sa.Column("normalized_name", sa.String(length=255), nullable=True))
    op.create_index(
        op.f("ix_venues_google_place_id"), "venues", ["google_place_id"], unique=True
    )
    op.create_index(op.f("ix_venues_normalized_name"), "venues", ["normalized_name"])

    day_enum = postgresql.ENUM(*DAY_VALUES, name="day_of_week")
    source_enum = postgresql.ENUM(*SOURCE_VALUES, name="extraction_source")
    confidence_enum = postgresql.ENUM(*CONFIDENCE_VALUES, name="confidence_level")
    day_enum.create(op.get_bind(), checkfirst=True)
    source_enum.create(op.get_bind(), checkfirst=True)
    confidence_enum.create(op.get_bind(), checkfirst=True)

    # ALTER ... USING text::enum_type validates each existing string against
    # the enum's allowed values; no backfill needed because the scanner
    # already writes matching strings ("Monday", "llm", "high").
    op.execute("ALTER TABLE happy_hours ALTER COLUMN days DROP DEFAULT")
    op.execute(
        "ALTER TABLE happy_hours ALTER COLUMN days "
        "TYPE day_of_week[] USING days::text::day_of_week[]"
    )
    op.execute("ALTER TABLE happy_hours ALTER COLUMN days SET DEFAULT '{}'::day_of_week[]")

    # Map legacy source strings to the new canonical enum values:
    #   "website_regex" -> "regex"
    #   "vision"        -> "llm_vision"
    op.execute(
        "ALTER TABLE happy_hours ALTER COLUMN source TYPE extraction_source USING "
        "(CASE source "
        " WHEN 'website_regex' THEN 'regex' "
        " WHEN 'vision' THEN 'llm_vision' "
        " ELSE source END)::extraction_source"
    )
    # Coerce unknown legacy confidence values to NULL rather than aborting.
    op.execute(
        "ALTER TABLE happy_hours ALTER COLUMN confidence TYPE confidence_level USING "
        "(CASE WHEN confidence IN ('high', 'medium', 'low') THEN confidence "
        " ELSE NULL END)::confidence_level"
    )


def downgrade() -> None:
    op.execute("ALTER TABLE happy_hours ALTER COLUMN days DROP DEFAULT")
    op.execute(
        "ALTER TABLE happy_hours ALTER COLUMN days "
        "TYPE varchar(16)[] USING days::text::varchar(16)[]"
    )
    op.execute(
        "ALTER TABLE happy_hours ALTER COLUMN days SET DEFAULT '{}'::character varying[]"
    )
    op.execute(
        "ALTER TABLE happy_hours ALTER COLUMN source TYPE varchar(32) USING source::text"
    )
    op.execute(
        "ALTER TABLE happy_hours ALTER COLUMN confidence TYPE varchar(16) USING confidence::text"
    )

    postgresql.ENUM(name="confidence_level").drop(op.get_bind(), checkfirst=True)
    postgresql.ENUM(name="extraction_source").drop(op.get_bind(), checkfirst=True)
    postgresql.ENUM(name="day_of_week").drop(op.get_bind(), checkfirst=True)

    op.drop_index(op.f("ix_venues_normalized_name"), table_name="venues")
    op.drop_index(op.f("ix_venues_google_place_id"), table_name="venues")
    op.drop_column("venues", "normalized_name")
    op.drop_column("venues", "google_place_id")
