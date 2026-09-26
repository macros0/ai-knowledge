"""Persist source file hash in generation checkpoints.

Revision ID: fd0b1c2d3e4f
Revises: fc0b1c2d3e4f
Create Date: 2026-09-25 22:20:00.000000
"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa

revision: str = "fd0b1c2d3e4f"
down_revision: Union[str, Sequence[str], None] = "fc0b1c2d3e4f"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    with op.batch_alter_table("document_staging") as batch_op:
        batch_op.add_column(sa.Column("source_file_hash", sa.String(length=64), nullable=True))


def downgrade() -> None:
    with op.batch_alter_table("document_staging") as batch_op:
        batch_op.drop_column("source_file_hash")
