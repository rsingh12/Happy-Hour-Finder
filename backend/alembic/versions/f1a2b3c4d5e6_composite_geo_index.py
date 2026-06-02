"""composite (latitude, longitude) index for venues

Revision ID: f1a2b3c4d5e6
Revises: e29971033f2c
Create Date: 2026-05-21 14:00:00.000000

The cascade matcher's step-4 fuzzy fallback runs a bounding-box query
(`latitude BETWEEN ? AND ? AND longitude BETWEEN ? AND ?`). With two
single-column B-tree indexes Postgres can only use one and filters the
other in memory — fine in low-density areas but expensive at Manhattan
density where a 0.02° lat stripe spans thousands of rows.
"""
from typing import Sequence, Union

from alembic import op


revision: str = "f1a2b3c4d5e6"
down_revision: Union[str, None] = "e29971033f2c"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.create_index(
        "ix_venues_lat_lng", "venues", ["latitude", "longitude"]
    )


def downgrade() -> None:
    op.drop_index("ix_venues_lat_lng", table_name="venues")
