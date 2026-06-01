"""Initial schema.

Creates every table from the SQLAlchemy metadata, including all indexes defined on
the models. Generating from metadata keeps this migration in lockstep with the
models and avoids hand-maintained DDL drift for the first revision.
"""

from __future__ import annotations

from alembic import op

from app.db import models  # noqa: F401 - register tables on the metadata
from app.db.base import Base

revision = "0001_initial"
down_revision = None
branch_labels = None
depends_on = None


def upgrade() -> None:
    bind = op.get_bind()
    Base.metadata.create_all(bind=bind)


def downgrade() -> None:
    bind = op.get_bind()
    Base.metadata.drop_all(bind=bind)
