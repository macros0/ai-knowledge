"""Add diagnostics control metadata, separate from business job queues."""
from alembic import op
import sqlalchemy as sa

revision = "030b1c2d3e4f"
down_revision = "021b1c2d3e4f"
branch_labels = None
depends_on = None


def upgrade():
    op.create_table(
        "diagnostic_sessions",
        sa.Column("id", sa.String(36), primary_key=True),
        sa.Column("active_slot", sa.Integer(), nullable=True, unique=True),
        sa.Column("status", sa.String(16), nullable=False),
        sa.Column("scope", sa.String(20), nullable=False),
        sa.Column("doc_id", sa.String(32), nullable=True),
        sa.Column("boot_id", sa.String(36), nullable=False),
        sa.Column("created_by_id", sa.String(255), nullable=False),
        sa.Column("created_by", sa.String(255), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("expires_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("stopped_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("stop_reason", sa.String(32), nullable=True),
        sa.Column("bytes_written", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("counts", sa.JSON(), nullable=False, server_default="{}"),
        sa.Column("participants", sa.JSON(), nullable=False, server_default="{}"),
        sa.Column("invitations", sa.JSON(), nullable=False, server_default="{}"),
        sa.Column("audit_receipts", sa.JSON(), nullable=False, server_default="[]"),
        sa.Column("schema_version", sa.Integer(), nullable=False, server_default="1"),
        sa.CheckConstraint("active_slot IS NULL OR active_slot = 1", name="diagnostic_session_single_slot"),
        sa.CheckConstraint("status IN ('starting','active','stopped')", name="diagnostic_session_status"),
    )
    for column in ("status", "created_at", "expires_at"):
        op.create_index(f"ix_diagnostic_sessions_{column}", "diagnostic_sessions", [column])
    op.create_table(
        "diagnostic_bundles",
        sa.Column("id", sa.String(36), primary_key=True),
        sa.Column("status", sa.String(16), nullable=False),
        sa.Column("created_by_id", sa.String(255), nullable=False),
        sa.Column("created_by", sa.String(255), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("finished_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("cutoff_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("expires_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("request", sa.JSON(), nullable=False),
        sa.Column("manifest", sa.JSON(), nullable=False, server_default="{}"),
        sa.Column("counts", sa.JSON(), nullable=False, server_default="{}"),
        sa.Column("size_bytes", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("sha256", sa.String(64), nullable=True),
        sa.Column("error_code", sa.String(64), nullable=True),
        sa.Column("audit_receipts", sa.JSON(), nullable=False, server_default="[]"),
        sa.Column("schema_version", sa.Integer(), nullable=False, server_default="1"),
    )
    for column in ("status", "created_at", "expires_at"):
        op.create_index(f"ix_diagnostic_bundles_{column}", "diagnostic_bundles", [column])


def downgrade():
    op.drop_table("diagnostic_bundles")
    op.drop_table("diagnostic_sessions")
