"""Add the domain glossary registry and retrieval history metadata.

Revision ID: b2c3d4e5f6a7
Revises: a1b2c3d4e5f6
Create Date: 2026-09-11
"""

from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


revision: str = "b2c3d4e5f6a7"
down_revision: Union[str, Sequence[str], None] = "a1b2c3d4e5f6"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.create_table(
        "domain_terms",
        sa.Column("id", sa.Integer(), autoincrement=True, nullable=False),
        sa.Column("canonical", sa.String(length=128), nullable=False),
        sa.Column("kind", sa.String(length=48), nullable=False),
        sa.Column("original_name", sa.String(length=256), nullable=False),
        sa.Column("original_description", sa.Text(), nullable=True),
        sa.Column("canonical_locale", sa.String(length=16), server_default="und", nullable=False),
        sa.Column("enabled", sa.Boolean(), server_default=sa.text("true"), nullable=False),
        sa.Column("version", sa.Integer(), server_default="1", nullable=False),
        sa.Column("source_revision", sa.Integer(), server_default="1", nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("created_by", sa.String(length=255), nullable=True),
        sa.Column("updated_by", sa.String(length=255), nullable=True),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("canonical"),
    )
    op.create_table(
        "domain_term_translations",
        sa.Column("term_id", sa.Integer(), nullable=False),
        sa.Column("locale", sa.String(length=16), nullable=False),
        sa.Column("display_name", sa.String(length=256), nullable=False),
        sa.Column("description", sa.Text(), nullable=True),
        sa.Column("source_revision", sa.Integer(), nullable=False),
        sa.Column("version", sa.Integer(), server_default="1", nullable=False),
        sa.Column("is_machine_translated", sa.Boolean(), server_default=sa.text("false"), nullable=False),
        sa.Column("reviewed_by", sa.String(length=255), nullable=True),
        sa.Column("reviewed_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("updated_by", sa.String(length=255), nullable=True),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
        sa.ForeignKeyConstraint(["term_id"], ["domain_terms.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("term_id", "locale"),
    )
    op.create_table(
        "domain_term_aliases",
        sa.Column("id", sa.Integer(), autoincrement=True, nullable=False),
        sa.Column("term_id", sa.Integer(), nullable=False),
        sa.Column("alias", sa.String(length=256), nullable=False),
        sa.Column("normalized_alias", sa.String(length=256), nullable=False),
        sa.Column("locale", sa.String(length=16), nullable=True),
        sa.Column("auto_expand", sa.Boolean(), server_default=sa.text("false"), nullable=False),
        sa.Column("search_enabled", sa.Boolean(), server_default=sa.text("false"), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("created_by", sa.String(length=255), nullable=True),
        sa.Column("updated_by", sa.String(length=255), nullable=True),
        sa.ForeignKeyConstraint(["term_id"], ["domain_terms.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("normalized_alias"),
    )
    op.create_index("ix_domain_term_aliases_term_id", "domain_term_aliases", ["term_id"])
    op.add_column(
        "chat_messages",
        sa.Column("retrieval_metadata", sa.JSON(), nullable=True),
    )


def downgrade() -> None:
    op.drop_column("chat_messages", "retrieval_metadata")
    op.drop_index("ix_domain_term_aliases_term_id", table_name="domain_term_aliases")
    op.drop_table("domain_term_aliases")
    op.drop_table("domain_term_translations")
    op.drop_table("domain_terms")
