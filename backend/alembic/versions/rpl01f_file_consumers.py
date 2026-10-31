"""Independent protocol markers for durable non-generation file consumers."""
from alembic import op
import sqlalchemy as sa

revision = "rpl01f_file_consumers"
down_revision = "rpl01e_source_caches"
branch_labels = None
depends_on = None


def upgrade():
    op.add_column("diagnostic_bundles", sa.Column("artifact_version", sa.Integer(), nullable=False, server_default="1"))
    op.add_column("document_processing_reviews", sa.Column("artifact_version", sa.Integer(), nullable=False, server_default="1"))
    op.add_column("document_processing_reviews", sa.Column("artifact_revision", sa.String(32), nullable=True))


def downgrade():
    jobs = sa.table("jobs", sa.column("job_type"), sa.column("status"), sa.column("result", sa.JSON))
    if op.get_bind().scalar(sa.select(sa.func.count()).select_from(jobs).where(jobs.c.job_type == "bulk_export",
            jobs.c.status == "completed", jobs.c.result["artifact_version"].as_integer() == 2,
            jobs.c.result["artifact_status"].as_string() == "available")):
        raise RuntimeError("Cannot downgrade while a portable export is available")
    if op.get_bind().scalar(sa.text("SELECT count(*) FROM diagnostic_bundles WHERE artifact_version=2")):
        raise RuntimeError("Cannot drop portable diagnostic bundle marker")
    if op.get_bind().scalar(sa.text("SELECT count(*) FROM document_processing_reviews WHERE artifact_version=2")):
        raise RuntimeError("Cannot drop portable review marker")
    op.drop_column("document_processing_reviews", "artifact_revision")
    op.drop_column("document_processing_reviews", "artifact_version")
    op.drop_column("diagnostic_bundles", "artifact_version")
