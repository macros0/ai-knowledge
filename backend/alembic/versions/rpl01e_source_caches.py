"""Portable source cache heads and independent document protocol marker."""
from alembic import op
import sqlalchemy as sa

revision = "rpl01e_source_caches"
down_revision = "rpl01d_portable_staging"
branch_labels = None
depends_on = None


def upgrade():
    op.add_column("documents", sa.Column("source_cache_version", sa.Integer(), nullable=False, server_default="1"))
    op.create_table("artifact_source_caches",
        sa.Column("doc_id", sa.String(16), sa.ForeignKey("documents.id", ondelete="CASCADE"), primary_key=True),
        sa.Column("generation_id", sa.String(32), primary_key=True, server_default=""),
        sa.Column("cache_id", sa.String(64), primary_key=True),
        sa.Column("source_key", sa.String(64), nullable=False),
        sa.Column("content_fingerprint", sa.String(64), nullable=False),
        sa.Column("object_id", sa.String(32), sa.ForeignKey("artifact_objects.object_id"), nullable=False))


def downgrade():
    if op.get_bind().scalar(sa.text("SELECT count(*) FROM documents WHERE source_cache_version=2")):
        raise RuntimeError("Cannot drop portable source cache protocol marker")
    op.drop_table("artifact_source_caches")
    op.drop_column("documents", "source_cache_version")
