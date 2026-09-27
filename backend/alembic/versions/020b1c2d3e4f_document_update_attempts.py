"""Retain published state and durable cancellation of document updates."""
from alembic import op
import sqlalchemy as sa

revision = "020b1c2d3e4f"
down_revision = "010b1c2d3e4f"
branch_labels = None
depends_on = None


def upgrade():
    op.create_table(
        "document_update_attempts",
        sa.Column("doc_id", sa.String(32), sa.ForeignKey("documents.id", ondelete="CASCADE"), primary_key=True),
        sa.Column("id", sa.String(32), nullable=False, unique=True),
        sa.Column("base_generation_id", sa.String(32), nullable=True),
        sa.Column("previous_fields", sa.JSON(), nullable=False),
        sa.Column("cancel_requested", sa.Boolean(), nullable=False),
    )


def downgrade():
    op.drop_table("document_update_attempts")
