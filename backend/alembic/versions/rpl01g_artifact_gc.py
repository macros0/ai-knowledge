"""Durable GC scope, inventory cursors and orphan quarantine."""
from alembic import op
import sqlalchemy as sa

revision = "rpl01g_artifact_gc"
down_revision = "rpl01f_file_consumers"
branch_labels = None
depends_on = None


def upgrade():
    op.create_index("ix_artifact_gc_candidates", "artifact_objects", ["store_id", "state", "object_id"])
    op.create_index("ix_artifact_source_caches_object_id", "artifact_source_caches", ["object_id"])
    op.create_table("artifact_gc_state",
        sa.Column("store_id", sa.String(128), primary_key=True),
        sa.Column("scope_token", sa.String(32), nullable=False),
        sa.Column("location_hash", sa.String(64), nullable=False),
        sa.Column("inventory_cursor", sa.Text(), nullable=True),
        sa.Column("orphan_cursor", sa.String(32), nullable=False, server_default=""),
        sa.Column("sweep_cursor", sa.String(32), nullable=False, server_default=""),
        sa.Column("owner_token", sa.String(32), nullable=True),
        sa.Column("lease_expires_at", sa.DateTime(timezone=True), nullable=True))
    op.create_table("artifact_orphans",
        sa.Column("object_id", sa.String(32), primary_key=True),
        sa.Column("store_id", sa.String(128), nullable=False),
        sa.Column("size", sa.BigInteger(), nullable=False),
        sa.Column("fingerprint", sa.String(64), nullable=False),
        sa.Column("first_seen_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.current_timestamp()),
        sa.CheckConstraint("size >= 0", name="ck_artifact_orphan_size"))
    op.create_index("ix_artifact_orphans_store_id", "artifact_orphans", ["store_id"])


def downgrade():
    if op.get_bind().scalar(sa.text("SELECT count(*) FROM artifact_gc_state")):
        raise RuntimeError("Disable GC and preserve its database/storage ownership before downgrade")
    op.drop_index("ix_artifact_orphans_store_id", table_name="artifact_orphans")
    op.drop_table("artifact_orphans")
    op.drop_table("artifact_gc_state")
    op.drop_index("ix_artifact_source_caches_object_id", table_name="artifact_source_caches")
    op.drop_index("ix_artifact_gc_candidates", table_name="artifact_objects")
