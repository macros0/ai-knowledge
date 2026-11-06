"""Bind one leased chat execution to one accepted assistant history row."""
from alembic import op
import sqlalchemy as sa

revision = "rpl03a_chat_runs"
down_revision = "rpl03_replica_coordination"
branch_labels = None
depends_on = None


def upgrade():
    op.create_table("chat_runs",
        sa.Column("id", sa.String(32), primary_key=True),
        sa.Column("actor_id", sa.String(255), nullable=False),
        sa.Column("attempt_id", sa.String(36), nullable=False),
        sa.Column("request_hash", sa.String(64), nullable=False),
        sa.Column("session_id", sa.String(36), sa.ForeignKey("chat_sessions.id", ondelete="CASCADE"), nullable=False),
        sa.Column("message_id", sa.Integer(), sa.ForeignKey("chat_messages.id", ondelete="CASCADE"), nullable=False, unique=True),
        sa.Column("owner_id", sa.String(128), nullable=False),
        sa.Column("fence", sa.BigInteger(), nullable=False),
        sa.Column("lease_until", sa.DateTime(timezone=True), nullable=False),
        sa.Column("cancel_requested", sa.Boolean(), nullable=False, server_default=sa.false()),
        sa.Column("status", sa.String(16), nullable=False),
        sa.Column("capacity_id", sa.String(32), nullable=False),
        sa.Column("capacity_fence", sa.BigInteger(), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("finished_at", sa.DateTime(timezone=True), nullable=True),
        sa.UniqueConstraint("actor_id", "attempt_id", name="uq_chat_actor_attempt"),
        sa.CheckConstraint("fence >= 1 AND capacity_fence >= 1", name="ck_chat_run_fence"),
        sa.CheckConstraint("status IN ('running','completed','stopped','failed','interrupted')", name="ck_chat_run_status"))
    op.create_index("ix_chat_runs_session_id", "chat_runs", ["session_id"])
    op.create_index("ix_chat_run_expired", "chat_runs", ["status", "lease_until"])


def downgrade():
    if op.get_bind().scalar(sa.text("SELECT count(*) FROM chat_runs")):
        raise RuntimeError("Accepted chat runs require coherent recovery")
    op.drop_table("chat_runs")
