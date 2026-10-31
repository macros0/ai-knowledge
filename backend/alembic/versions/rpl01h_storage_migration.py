"""Installation identity, offline migration receipts and portable legacy pointers."""
import json
from uuid import uuid4
from alembic import op
import sqlalchemy as sa

revision = "rpl01h_storage_migration"
down_revision = "rpl01g_artifact_gc"
branch_labels = None
depends_on = None


def upgrade():
    op.create_table("artifact_storage_control",
        sa.Column("id", sa.Integer(), primary_key=True), sa.Column("installation_id", sa.String(32), nullable=False),
        sa.Column("active_store_id", sa.String(128)), sa.Column("active_descriptor", sa.JSON()),
        sa.Column("readonly_roots", sa.JSON(), nullable=False), sa.Column("maintenance_token", sa.String(32)),
        sa.Column("migration_id", sa.String(32)), sa.CheckConstraint("id = 1", name="ck_artifact_storage_singleton"))
    table = sa.table("artifact_storage_control", sa.column("id", sa.Integer()), sa.column("installation_id", sa.String()), sa.column("readonly_roots", sa.JSON()))
    op.bulk_insert(table, [{"id": 1, "installation_id": uuid4().hex, "readonly_roots": json.loads("[]")}])
    op.create_table("artifact_storage_migrations",
        sa.Column("id", sa.String(32), primary_key=True), sa.Column("installation_id", sa.String(32), nullable=False),
        sa.Column("plan_hash", sa.String(64), nullable=False), sa.Column("source_revision", sa.String(64), nullable=False),
        sa.Column("source_store_id", sa.String(128), nullable=False), sa.Column("target_store_id", sa.String(128), nullable=False),
        sa.Column("status", sa.String(16), nullable=False), sa.Column("backup_hash", sa.String(64), nullable=False),
        sa.Column("receipts", sa.JSON(), nullable=False), sa.Column("verified_hash", sa.String(64)),
        sa.CheckConstraint("status IN ('copying','verified','cutover')", name="ck_artifact_storage_migration_status"))
    op.add_column("documents", sa.Column("legacy_artifacts_id", sa.String(32)))
    op.add_column("documents", sa.Column("legacy_artifacts_version", sa.Integer(), nullable=False, server_default="1"))
    op.create_index("ix_documents_legacy_artifacts_id", "documents", ["legacy_artifacts_id"])
    op.add_column("document_generations", sa.Column("migration_artifacts_id", sa.String(32)))
    op.add_column("document_generations", sa.Column("migration_artifacts_version", sa.Integer(), nullable=False, server_default="1"))
    op.create_index("ix_document_generations_migration_artifacts_id", "document_generations", ["migration_artifacts_id"])


def downgrade():
    if op.get_bind().scalar(sa.text("SELECT count(*) FROM artifact_storage_migrations")):
        raise RuntimeError("Storage migration receipts/selection require explicit recovery, not schema downgrade")
    if op.get_bind().scalar(sa.text("SELECT count(*) FROM artifact_storage_control WHERE active_store_id IS NOT NULL OR maintenance_token IS NOT NULL")):
        raise RuntimeError("Storage selection cannot be discarded")
    for table, prefix in (("documents", "legacy"), ("document_generations", "migration")):
        if op.get_bind().scalar(sa.text(f"SELECT count(*) FROM {table} WHERE {prefix}_artifacts_id IS NOT NULL OR {prefix}_artifacts_version != 1")):
            raise RuntimeError("Portable snapshot pointers cannot be discarded")
    op.drop_index("ix_document_generations_migration_artifacts_id", table_name="document_generations")
    op.drop_column("document_generations", "migration_artifacts_id")
    op.drop_column("document_generations", "migration_artifacts_version")
    op.drop_index("ix_documents_legacy_artifacts_id", table_name="documents")
    op.drop_column("documents", "legacy_artifacts_id")
    op.drop_column("documents", "legacy_artifacts_version")
    op.drop_table("artifact_storage_migrations")
    op.drop_table("artifact_storage_control")
