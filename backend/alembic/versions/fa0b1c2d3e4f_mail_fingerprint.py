"""Persist complete standalone-mail fingerprint for similar-document review.

Revision ID: fa0b1c2d3e4f
Revises: f9a0b1c2d3e4
Create Date: 2026-09-25 19:30:00.000000
"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


revision: str = "fa0b1c2d3e4f"
down_revision: Union[str, Sequence[str], None] = "f9a0b1c2d3e4"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    with op.batch_alter_table("documents") as batch_op:
        batch_op.add_column(sa.Column("mail_fingerprint", sa.String(length=64), nullable=True))
        batch_op.create_index("ix_documents_mail_fingerprint", ["mail_fingerprint"], unique=False)


def downgrade() -> None:
    with op.batch_alter_table("documents") as batch_op:
        batch_op.drop_index("ix_documents_mail_fingerprint")
        batch_op.drop_column("mail_fingerprint")
