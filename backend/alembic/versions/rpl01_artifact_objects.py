"""Immutable blob metadata, bindings and upload receipt tombstones."""
from alembic import op
import sqlalchemy as sa

revision = "rpl01_artifact_objects"
down_revision = "rpl00_diagnostic_ingest"
branch_labels = None
depends_on = None


def upgrade():
    op.create_table("artifact_objects",
        sa.Column("object_id", sa.String(32), primary_key=True),
        sa.Column("key", sa.String(40), nullable=False),
        sa.Column("sha256", sa.String(64), nullable=False),
        sa.Column("size", sa.BigInteger(), nullable=False),
        sa.Column("media_type", sa.String(255), nullable=False),
        sa.Column("store_id", sa.String(128), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.CheckConstraint("size >= 0", name="ck_artifact_size"))
    op.create_table("artifact_bindings",
        sa.Column("owner_type", sa.String(32), primary_key=True),
        sa.Column("owner_id", sa.String(64), primary_key=True),
        sa.Column("generation_id", sa.String(32), primary_key=True),
        sa.Column("logical_name", sa.String(512), primary_key=True),
        sa.Column("object_id", sa.String(32), sa.ForeignKey("artifact_objects.object_id"), nullable=False),
        sa.Column("doc_id", sa.String(16), sa.ForeignKey("documents.id"), nullable=True))
    op.create_index("ix_artifact_bindings_object_id", "artifact_bindings", ["object_id"])
    op.create_index("ix_artifact_bindings_doc_id", "artifact_bindings", ["doc_id"])
    op.create_table("upload_receipts",
        sa.Column("actor_id", sa.String(64), primary_key=True),
        sa.Column("idempotency_key", sa.String(64), primary_key=True),
        sa.Column("request_fingerprint", sa.String(64), nullable=False),
        sa.Column("document_id", sa.String(16), nullable=False),
        sa.Column("state", sa.String(16), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False))
    op.create_index("ix_upload_receipts_document_id", "upload_receipts", ["document_id"])


def downgrade():
    op.drop_table("upload_receipts")
    op.drop_table("artifact_bindings")
    op.drop_table("artifact_objects")
