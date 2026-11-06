"""Bounded immutable diagnostic evidence shared by all application instances."""
from alembic import op
import sqlalchemy as sa

revision = "rpl03b_diagnostics"
down_revision = "rpl03a_chat_runs"
branch_labels = None
depends_on = None


def upgrade():
    op.create_table("diagnostic_replica_state",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("policy_hash", sa.String(64), nullable=False),
        sa.Column("evicted_segments", sa.Integer(), nullable=False),
        sa.CheckConstraint("id = 1", name="ck_diagnostic_replica_singleton"))
    op.create_table("diagnostic_instances",
        sa.Column("id", sa.String(36), primary_key=True),
        sa.Column("role", sa.String(12), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("heartbeat_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("closed_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("counters", sa.JSON(), nullable=False),
        sa.CheckConstraint("role IN ('api','worker','combined','frontend')", name="ck_diagnostic_instance_role"))
    op.create_index("ix_diagnostic_instances_heartbeat_at", "diagnostic_instances", ["heartbeat_at"])
    op.create_table("diagnostic_segments",
        sa.Column("id", sa.String(36), primary_key=True),
        sa.Column("instance_id", sa.String(36), nullable=False),
        sa.Column("stream", sa.String(36), nullable=False),
        sa.Column("component", sa.String(12), nullable=False),
        sa.Column("payload", sa.Text(), nullable=False),
        sa.Column("sha256", sa.String(64), nullable=False),
        sa.Column("size", sa.Integer(), nullable=False),
        sa.Column("event_count", sa.Integer(), nullable=False),
        sa.Column("first_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("last_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("expires_at", sa.DateTime(timezone=True), nullable=False),
        sa.CheckConstraint("size > 0 AND size <= 65536 AND event_count > 0", name="ck_diagnostic_segment_size"),
        sa.CheckConstraint("component IN ('backend','frontend')", name="ck_diagnostic_segment_component"))
    for column in ("instance_id", "stream", "expires_at"):
        op.create_index("ix_diagnostic_segments_" + column, "diagnostic_segments", [column])
    op.create_index("ix_diagnostic_segment_window", "diagnostic_segments", ["stream", "created_at", "id"])


def downgrade():
    tables = ("diagnostic_segments", "diagnostic_instances", "diagnostic_replica_state")
    if any(op.get_bind().scalar(sa.text("SELECT count(*) FROM " + table)) for table in tables):
        raise RuntimeError("Accepted diagnostic evidence requires coherent recovery")
    for table in tables:
        op.drop_table(table)
