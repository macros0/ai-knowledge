"""Store hierarchical parser sources and source-owned chunks/attachments.

Revision ID: f9a0b1c2d3e4
Revises: d7e8f9a0b1c2
Create Date: 2026-09-25 18:35:00.000000
"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


revision: str = "f9a0b1c2d3e4"
down_revision: Union[str, Sequence[str], None] = "d7e8f9a0b1c2"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.create_table(
        "document_sources",
        sa.Column("doc_id", sa.String(length=16), nullable=False),
        sa.Column("source_id", sa.String(length=255), nullable=False),
        sa.Column("parent_source_id", sa.String(length=255), nullable=True),
        sa.Column("ordinal", sa.Integer(), nullable=False),
        sa.Column("kind", sa.String(length=64), nullable=False),
        sa.Column("display_name", sa.String(length=512), nullable=False),
        sa.Column("metadata", sa.JSON(), nullable=True),
        sa.Column("saved_path", sa.String(length=1024), nullable=True),
        sa.Column("extraction_status", sa.String(length=32), nullable=True),
        sa.Column("artifact_kind", sa.String(length=32), nullable=False),
        sa.Column("container_source_id", sa.String(length=255), nullable=True),
        sa.Column("container_locator", sa.String(length=1024), nullable=True),
        sa.Column("content_fingerprint", sa.String(length=64), nullable=True),
        sa.Column("parser_version", sa.String(length=64), nullable=True),
        sa.Column("warnings", sa.JSON(), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.ForeignKeyConstraint(["doc_id"], ["documents.id"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(
            ["doc_id", "parent_source_id"],
            ["document_sources.doc_id", "document_sources.source_id"],
            name="fk_document_sources_parent",
            ondelete="CASCADE",
        ),
        sa.PrimaryKeyConstraint("doc_id", "source_id"),
    )
    op.create_index("ix_document_sources_doc_parent", "document_sources", ["doc_id", "parent_source_id"])

    with op.batch_alter_table("document_chunks") as batch_op:
        batch_op.add_column(sa.Column("source_id", sa.String(length=255), nullable=True))
        batch_op.create_index("ix_document_chunks_source_id", ["source_id"])
    with op.batch_alter_table("okf_attachments") as batch_op:
        batch_op.add_column(sa.Column("source_id", sa.String(length=255), nullable=True))
        batch_op.create_index("ix_okf_attachments_source_id", ["source_id"])
    with op.batch_alter_table("okf_concepts") as batch_op:
        batch_op.add_column(sa.Column("source_id", sa.String(length=255), nullable=True))
        batch_op.create_index("ix_okf_concepts_source_id", ["source_id"])


def downgrade() -> None:
    with op.batch_alter_table("okf_concepts") as batch_op:
        batch_op.drop_index("ix_okf_concepts_source_id")
        batch_op.drop_column("source_id")
    with op.batch_alter_table("okf_attachments") as batch_op:
        batch_op.drop_index("ix_okf_attachments_source_id")
        batch_op.drop_column("source_id")
    with op.batch_alter_table("document_chunks") as batch_op:
        batch_op.drop_index("ix_document_chunks_source_id")
        batch_op.drop_column("source_id")
    op.drop_index("ix_document_sources_doc_parent", table_name="document_sources")
    op.drop_table("document_sources")
