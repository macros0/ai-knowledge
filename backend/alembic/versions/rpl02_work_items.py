"""Internal transactional work ownership; public bulk Job records are unchanged."""
from alembic import op
import sqlalchemy as sa

revision = "rpl02_work_items"
down_revision = "rpl01h_storage_migration"
branch_labels = None
depends_on = None


def upgrade():
    op.create_table("work_items",
        sa.Column("id", sa.String(32), primary_key=True),
        sa.Column("kind", sa.String(64), nullable=False),
        sa.Column("payload_version", sa.Integer(), nullable=False),
        sa.Column("payload_json", sa.JSON(), nullable=False),
        sa.Column("dedupe_key", sa.String(255), nullable=False, unique=True),
        sa.Column("doc_id", sa.String(64)),
        sa.Column("status", sa.String(16), nullable=False, server_default="queued"),
        sa.Column("owner_id", sa.String(128)),
        sa.Column("fence", sa.BigInteger(), nullable=False, server_default="0"),
        sa.Column("lease_until", sa.DateTime(timezone=True)),
        sa.Column("cancel_requested", sa.Boolean(), nullable=False, server_default=sa.false()),
        sa.Column("attempts", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("available_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.current_timestamp()),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.current_timestamp()),
        sa.Column("finished_at", sa.DateTime(timezone=True)),
        sa.Column("error_code", sa.String(64)),
        sa.CheckConstraint("status IN ('queued','running','succeeded','failed','cancelled')", name="ck_work_status"),
        sa.CheckConstraint("fence >= 0 AND attempts >= 0 AND payload_version >= 1", name="ck_work_counters"))
    op.create_index("ix_work_items_doc_id", "work_items", ["doc_id"])
    op.create_index("ix_work_claim", "work_items", ["status", "available_at", "created_at", "id"])
    op.create_index("ix_work_expired", "work_items", ["status", "lease_until"])


def downgrade():
    if op.get_bind().scalar(sa.text("SELECT count(*) FROM work_items")):
        raise RuntimeError("Accepted work ledger requires coherent recovery, not schema downgrade")
    op.drop_table("work_items")
