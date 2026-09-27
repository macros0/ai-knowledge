"""Align document_update_attempts.doc_id with documents.id.

Revision ID: 021b1c2d3e4f
Revises: 020b1c2d3e4f
Create Date: 2026-09-27

The first update-attempt migration declared its foreign key as VARCHAR(32),
while the referenced documents.id domain is VARCHAR(16). Keep the original
published migration intact and correct both existing and new installations.
"""
from alembic import op
import sqlalchemy as sa


revision = "021b1c2d3e4f"
down_revision = "020b1c2d3e4f"
branch_labels = None
depends_on = None


def upgrade() -> None:
    # batch_alter_table emits ALTER COLUMN on PostgreSQL and rebuilds the table
    # for SQLite, which keeps the schema-drift test representative of CI.
    with op.batch_alter_table("document_update_attempts") as batch_op:
        batch_op.alter_column(
            "doc_id",
            existing_type=sa.String(length=32),
            type_=sa.String(length=16),
            existing_nullable=False,
        )


def downgrade() -> None:
    with op.batch_alter_table("document_update_attempts") as batch_op:
        batch_op.alter_column(
            "doc_id",
            existing_type=sa.String(length=16),
            type_=sa.String(length=32),
            existing_nullable=False,
        )
