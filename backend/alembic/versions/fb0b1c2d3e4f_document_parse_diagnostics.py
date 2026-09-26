"""Persist parser diagnostics for resume and incomplete attachment reporting.

Revision ID: fb0b1c2d3e4f
Revises: fa0b1c2d3e4f
Create Date: 2026-09-25 20:15:00.000000
"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


revision: str = "fb0b1c2d3e4f"
down_revision: Union[str, Sequence[str], None] = "fa0b1c2d3e4f"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    with op.batch_alter_table("documents") as batch_op:
        batch_op.add_column(sa.Column("parser_version", sa.String(length=64), nullable=True))
        batch_op.add_column(sa.Column("parse_warnings", sa.JSON(), nullable=True))


def downgrade() -> None:
    with op.batch_alter_table("documents") as batch_op:
        batch_op.drop_column("parse_warnings")
        batch_op.drop_column("parser_version")
