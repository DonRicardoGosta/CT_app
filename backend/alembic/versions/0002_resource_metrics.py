"""Add resource_metrics table (CPU/RAM history per service).

Creates only the new table from the model metadata (checkfirst), keeping it in
lockstep with the ORM and avoiding hand-maintained DDL drift.
"""

from __future__ import annotations

from alembic import op

from app.db import models  # noqa: F401 - register tables on the metadata

revision = "0002_resource_metrics"
down_revision = "0001_initial"
branch_labels = None
depends_on = None


def upgrade() -> None:
    bind = op.get_bind()
    models.ResourceMetric.__table__.create(bind=bind, checkfirst=True)


def downgrade() -> None:
    bind = op.get_bind()
    models.ResourceMetric.__table__.drop(bind=bind, checkfirst=True)
