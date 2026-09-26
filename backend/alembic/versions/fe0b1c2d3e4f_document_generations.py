"""Add durable document generation publication state.

Revision ID: fe0b1c2d3e4f
Revises: fd0b1c2d3e4f
"""
from alembic import op
import sqlalchemy as sa

revision = "fe0b1c2d3e4f"
down_revision = "fd0b1c2d3e4f"
branch_labels = None
depends_on = None


def upgrade():
    op.create_table(
        "document_generation_states",
        sa.Column("doc_id", sa.String(16), nullable=False),
        sa.Column("active_generation_id", sa.String(32), nullable=True),
        sa.Column("candidate_generation_id", sa.String(32), nullable=True),
        sa.ForeignKeyConstraint(["doc_id"], ["documents.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("doc_id"),
    )
    op.create_table(
        "document_generations",
        sa.Column("id", sa.String(32), nullable=False),
        sa.Column("doc_id", sa.String(16), nullable=False),
        sa.Column("base_generation_id", sa.String(32), nullable=True),
        sa.Column("phase", sa.String(16), nullable=False),
        sa.Column("legacy_cleanup_pending", sa.Boolean(), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("activated_at", sa.DateTime(timezone=True), nullable=True),
        sa.CheckConstraint(
            "phase IN ('preparing', 'ready', 'active', 'retired', 'abandoned')",
            name="ck_document_generations_phase",
        ),
        sa.ForeignKeyConstraint(["doc_id"], ["documents.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index("ix_document_generations_doc_id", "document_generations", ["doc_id"])


def downgrade():
    op.drop_index("ix_document_generations_doc_id", table_name="document_generations")
    op.drop_table("document_generations")
    op.drop_table("document_generation_states")
