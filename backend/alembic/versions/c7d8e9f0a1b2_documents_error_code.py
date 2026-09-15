"""documents.error_code — стабильный код ошибки обработки

Revision ID: c7d8e9f0a1b2
Revises: d4e5f6a7b8c9
Create Date: 2026-09-15 22:00:00.000000
"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


revision: str = "c7d8e9f0a1b2"
down_revision: Union[str, Sequence[str], None] = "d4e5f6a7b8c9"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.add_column("documents", sa.Column("error_code", sa.String(length=64), nullable=True))


def downgrade() -> None:
    op.drop_column("documents", "error_code")
