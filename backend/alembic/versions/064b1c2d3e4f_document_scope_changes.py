"""Incremental processing scope receipts and stable display ordering."""
from alembic import op
import sqlalchemy as sa

revision = "064b1c2d3e4f"
down_revision = "063b1c2d3e4f"
branch_labels = None
depends_on = None


def upgrade():
    op.create_table("document_scope_edit_locks",
        sa.Column("doc_id", sa.String(16), sa.ForeignKey("documents.id", ondelete="CASCADE"), primary_key=True),
        sa.Column("owner_id", sa.String(255), nullable=False),
        sa.Column("owner_name", sa.String(255), nullable=False),
        sa.Column("token_hash", sa.String(64), nullable=False),
        sa.Column("expires_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("idle_expires_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("activity_sequence", sa.BigInteger(), nullable=False, server_default="0"))
    op.add_column("document_chunks", sa.Column("display_order", sa.Integer(), nullable=True))
    op.add_column("okf_concepts", sa.Column("display_order", sa.Integer(), nullable=True))
    op.add_column("document_generation_states", sa.Column("next_chunk_index", sa.Integer(), nullable=False, server_default="0"))
    op.create_table("document_scope_changes",
        sa.Column("id", sa.String(32), primary_key=True),
        sa.Column("doc_id", sa.String(16), sa.ForeignKey("documents.id", ondelete="CASCADE"), nullable=False),
        sa.Column("request_id", sa.String(32), nullable=True, unique=True),
        sa.Column("state", sa.String(24), nullable=False),
        sa.Column("plan_json", sa.JSON(), nullable=False),
        sa.Column("candidate_generation_id", sa.String(32), nullable=True),
        sa.Column("owner_token", sa.String(32), nullable=True),
        sa.Column("result_json", sa.JSON(), nullable=True),
        sa.Column("error_code", sa.String(64), nullable=True),
        sa.Column("created_by", sa.String(255), nullable=True),
        *[sa.Column(name, sa.DateTime(timezone=True), nullable=name != "created_at")
          for name in ("lease_expires_at", "created_at", "expires_at", "started_at", "finished_at")],
        sa.CheckConstraint("state IN ('preview','admitted','queued','processing','copying','ready','published','failed','canceled')",
                           name="ck_document_scope_changes_state"))
    op.create_index("ix_document_scope_changes_doc_id", "document_scope_changes", ["doc_id"])


def downgrade():
    # Old applications cannot safely hydrate/reindex a publication with holes or
    # forget accepted idempotency receipts. Do not silently discard that state.
    if op.get_bind().scalar(sa.text("SELECT COUNT(*) FROM document_scope_changes WHERE state != 'preview'")):
        raise RuntimeError("Applied scope changes require a compatible application")
    op.drop_index("ix_document_scope_changes_doc_id", table_name="document_scope_changes")
    op.drop_table("document_scope_changes")
    op.drop_table("document_scope_edit_locks")
    op.drop_column("document_generation_states", "next_chunk_index")
    op.drop_column("okf_concepts", "display_order")
    op.drop_column("document_chunks", "display_order")
