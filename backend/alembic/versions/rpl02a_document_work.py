"""Bind immutable generation namespaces to durable work attempts."""
from alembic import op
import sqlalchemy as sa

revision = "rpl02a_document_work"
down_revision = "rpl02_work_items"
branch_labels = None
depends_on = None


def upgrade():
    op.create_table("document_work_inputs", sa.Column("work_id", sa.String(32), sa.ForeignKey("work_items.id"), primary_key=True),
        sa.Column("doc_id", sa.String(64), sa.ForeignKey("documents.id", ondelete="CASCADE"), nullable=False),
        sa.Column("processing_scope_json", sa.JSON(none_as_null=True)), sa.Column("scope_request_id", sa.String(32), unique=True))
    op.create_index("ix_document_work_inputs_doc_id", "document_work_inputs", ["doc_id"])
    op.create_table("deleted_work_generations", sa.Column("generation_id", sa.String(32), primary_key=True),
        sa.Column("doc_id", sa.String(64), nullable=False), sa.Column("work_id", sa.String(32), nullable=False),
        sa.Column("work_fence", sa.BigInteger(), nullable=False))
    op.create_table("work_admission_state", sa.Column("id", sa.Integer(), primary_key=True),
                    sa.CheckConstraint("id = 1", name="ck_work_admission_singleton"))
    op.execute(sa.text("INSERT INTO work_admission_state(id) VALUES(1)"))
    op.add_column("document_generations", sa.Column("work_id", sa.String(32)))
    op.add_column("document_generations", sa.Column("work_fence", sa.BigInteger()))
    op.add_column("document_generations", sa.Column("work_cleanup_done", sa.Boolean(), nullable=False, server_default=sa.false()))
    op.create_index("ix_document_generations_work_id", "document_generations", ["work_id"])
    op.create_index("ix_generation_work_attempt", "document_generations", ["work_id", "work_fence"], unique=True)


def downgrade():
    if op.get_bind().scalar(sa.text("SELECT count(*) FROM document_work_inputs")):
        raise RuntimeError("Document work fencing inputs require coherent recovery")
    if op.get_bind().scalar(sa.text("SELECT count(*) FROM deleted_work_generations")):
        raise RuntimeError("Deleted document work fencing requires coherent recovery")
    if op.get_bind().scalar(sa.text("SELECT count(*) FROM work_items WHERE kind='document'")):
        raise RuntimeError("Accepted document work fencing requires coherent recovery")
    if op.get_bind().scalar(sa.text("SELECT count(*) FROM document_generations WHERE work_id IS NOT NULL OR work_fence IS NOT NULL OR work_cleanup_done")):
        raise RuntimeError("Document work fencing/tombstones require coherent recovery")
    op.drop_index("ix_generation_work_attempt", table_name="document_generations")
    op.drop_index("ix_document_generations_work_id", table_name="document_generations")
    for column in ("work_cleanup_done", "work_fence", "work_id"):
        op.drop_column("document_generations", column)
    op.drop_table("work_admission_state")
    op.drop_table("deleted_work_generations")
    op.drop_index("ix_document_work_inputs_doc_id", table_name="document_work_inputs")
    op.drop_table("document_work_inputs")
