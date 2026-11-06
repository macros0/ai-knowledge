"""Durable bounded frontend diagnostic inbox."""
from alembic import op
import sqlalchemy as sa

revision = "rpl00_diagnostic_ingest"
down_revision = "068b1c2d3e4f"
branch_labels = None
depends_on = None


def upgrade():
    op.create_table(
        "diagnostic_ingest_batches",
        sa.Column("instance_id", sa.String(36), primary_key=True),
        sa.Column("batch_id", sa.String(36), primary_key=True),
        sa.Column("payload_hash", sa.String(64), nullable=False),
        sa.Column("payload", sa.JSON(), nullable=False),
        sa.Column("payload_bytes", sa.Integer(), nullable=False),
        sa.Column("accepted", sa.Integer(), nullable=False),
        sa.Column("delivered", sa.Boolean(), nullable=False),
        sa.Column("next_attempt_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
    )
    op.create_index("ix_diagnostic_ingest_batches_created_at", "diagnostic_ingest_batches", ["created_at"])


def downgrade():
    op.drop_table("diagnostic_ingest_batches")
