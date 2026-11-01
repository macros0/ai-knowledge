"""Shared capacity, rolling rate windows and transactional cache revisions."""
from alembic import op
import sqlalchemy as sa

revision = "rpl03_replica_coordination"
down_revision = "rpl02b_work_progress"
branch_labels = None
depends_on = None


def upgrade():
    op.create_table("replica_capacity_resources",
        sa.Column("resource", sa.String(64), primary_key=True),
        sa.Column("capacity_limit", sa.Integer(), nullable=False),
        sa.CheckConstraint("capacity_limit >= 1 AND capacity_limit <= 10000", name="ck_replica_capacity_limit"))
    op.create_table("replica_capacity_slots",
        sa.Column("id", sa.String(32), primary_key=True),
        sa.Column("resource", sa.String(64), sa.ForeignKey("replica_capacity_resources.resource"), nullable=False),
        sa.Column("slot", sa.Integer(), nullable=False),
        sa.Column("owner_id", sa.String(128), nullable=True),
        sa.Column("fence", sa.BigInteger(), nullable=False),
        sa.Column("expires_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("protected_until", sa.DateTime(timezone=True), nullable=True),
        sa.Column("release_required", sa.Boolean(), nullable=False, server_default=sa.false()),
        sa.UniqueConstraint("resource", "slot", name="uq_replica_capacity_slot"),
        sa.CheckConstraint("slot >= 0 AND fence >= 1", name="ck_replica_capacity_slot"))
    op.create_table("replica_rate_buckets",
        sa.Column("key_hash", sa.String(64), primary_key=True),
        sa.Column("request_limit", sa.Integer(), nullable=False),
        sa.Column("window_seconds", sa.Integer(), nullable=False),
        sa.CheckConstraint("request_limit >= 1 AND window_seconds >= 1", name="ck_replica_rate_policy"))
    op.create_table("replica_rate_events",
        sa.Column("id", sa.String(32), primary_key=True),
        sa.Column("key_hash", sa.String(64), sa.ForeignKey("replica_rate_buckets.key_hash"), nullable=False),
        sa.Column("accepted_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("cost", sa.Integer(), nullable=False, server_default="1"),
        sa.CheckConstraint("cost >= 1 AND cost <= 10000", name="ck_replica_rate_cost"))
    op.create_index("ix_replica_rate_window", "replica_rate_events", ["key_hash", "accepted_at"])
    op.create_table("config_revisions",
        sa.Column("domain", sa.String(64), primary_key=True),
        sa.Column("revision", sa.BigInteger(), nullable=False),
        sa.CheckConstraint("revision >= 0", name="ck_config_revision"))


def downgrade():
    bind = op.get_bind()
    if (bind.scalar(sa.text("SELECT count(*) FROM replica_capacity_slots WHERE owner_id IS NOT NULL"))
            or bind.scalar(sa.text("SELECT count(*) FROM replica_rate_events"))
            or bind.scalar(sa.text("SELECT count(*) FROM config_revisions"))):
        raise RuntimeError("Replica coordination state requires coherent recovery")
    for table in ("config_revisions", "replica_rate_events", "replica_rate_buckets",
            "replica_capacity_slots", "replica_capacity_resources"):
        op.drop_table(table)
